import json as _json
import logging
import uuid
from datetime import date, datetime
from typing import Annotated, Literal, Optional

from langchain.tools import tool
from langchain_core.messages import SystemMessage, merge_message_runs
from langgraph.prebuilt.tool_node import InjectedState, InjectedStore
from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field, field_validator
from trustcall import create_extractor

from ainote.agents.models import get_model
from ainote.agents.prompts import CREATE_INSTRUCTIONS, TRUSTCALL_INSTRUCTION
from ainote.agents.graph.state import AgentState
from ainote.agents.memory import (
    get_instructions,
    get_profile,
    get_tasks,
    put_instructions,
    put_profile,
)


class Profile(BaseModel):
    """Profile of the user the agent is chatting with."""

    name: Optional[str] = Field(default=None, description="The user's name")
    gender: Optional[str] = Field(default=None, description="The user's gender")
    age: Optional[int] = Field(default=None, description="The user's age in years")
    job: Optional[str] = Field(default=None, description="The user's job or profession")
    location: Optional[str] = Field(default=None, description="The user's location")
    description: Optional[str] = Field(
        default=None, description="Short description of the user"
    )


class Task(BaseModel):
    """A task the user wants to track."""

    title: str = Field(description="The task title")
    description: Optional[str] = Field(default=None, description="Task details")
    tag: Literal["work", "personal"] = Field(
        default="personal",
        description="Task category: work or personal",
    )
    assignee: Optional[str] = Field(default=None, description="Who should own the task")
    priority: Literal["P0", "P1", "P2"] = Field(
        default="P1", description="P0 = urgent today, P1 = important, P2 = routine"
    )
    time: Optional[date] = Field(
        default=None,
        description="Concrete start date of the task (ISO format, e.g. 2026-09-07)",
    )

    @field_validator("time", mode="before")
    @classmethod
    def _parse_time(cls, v):
        """Coerce loose date strings (e.g. '9/7/2026', 'September 7, 2026')
        to ``date``. LLM output for the ``time`` field isn't guaranteed to be
        strict ISO, so accept common formats instead of letting validation
        bounce back into TrustCall's retry loop.
        """
        if v is None or isinstance(v, date):
            return v
        if isinstance(v, datetime):
            return v.date()
        if not isinstance(v, str):
            return v
        s = v.strip()
        try:
            return date.fromisoformat(s)  # 2026-09-07
        except ValueError:
            pass
        for fmt in (
            "%m/%d/%Y",      # 9/7/2026, 09/07/2026
            "%d/%m/%Y",      # 7/9/2026 (day-first, only reached if month>12)
            "%m-%d-%Y",      # 09-07-2026
            "%Y-%m-%d",      # 2026-9-7
            "%Y/%m/%d",      # 2026/9/7
            "%Y.%m.%d",      # 2026.9.7
            "%b %d, %Y",     # Sep 7, 2026
            "%B %d, %Y",     # September 7, 2026
        ):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return v  # 让 Pydantic 抛原生错误，TrustCall 校验循环可感知
    deadline: Optional[str] = Field(
        default=None,
        description="Deadline as YYYY-MM-DD or descriptive text",
    )
    pre_task: Optional[str] = Field(
        default=None,
        description="Prerequisite task title, if any",
    )
    status: Literal["not started", "in progress", "done", "archived"] = Field(
        default="not started", description="Current task status"
    )
    recurrence: Optional[str] = Field(
        default=None,
        description=(
            "Recurrence rule for recurring tasks, if any. "
            "Use 'daily' for every day, or 'weekly:mon,wed,fri' for specific "
            "weekdays (comma-separated, English 3-letter lowercase). "
            "Single one-off tasks leave this null."
        ),
    )

logger = logging.getLogger(__name__)


def _user_id(state: AgentState) -> str:
    return state.get("user_id") or "default"


@tool
async def update_profile(
    state: Annotated[AgentState, InjectedState()],
    store: Annotated[BaseStore, InjectedStore()],
) -> str:
    """Update the user's profile memory based on the conversation.

    Call this when the user provides personal information such as name,
    gender, job, location, or a short self-description.
    """
    user_id = _user_id(state)
    namespace = ("profile", user_id)

    existing_items = store.search(namespace)
    existing_memories = (
        [(item.key, "Profile", item.value) for item in existing_items]
        if existing_items
        else None
    )

    instruction = TRUSTCALL_INSTRUCTION.format(time=datetime.now().isoformat())
    updated_messages = list(
        merge_message_runs(
            messages=[SystemMessage(content=instruction)] + state["messages"][:-1]
        )
    )

    extractor = create_extractor(
        get_model(),
        tools=[Profile],
        tool_choice="Profile",
    )
    result = await extractor.ainvoke(
        {"messages": updated_messages, "existing": existing_memories},
        {"recursion_limit": 25},
    )

    for response, metadata in zip(result["responses"], result["response_metadata"]):
        key = metadata.get("json_doc_id", str(uuid.uuid4()))
        put_profile(store, user_id, response.model_dump(mode="json"), key=key)

    return "Profile memory updated."


@tool
async def update_tasks(
    state: Annotated[AgentState, InjectedState()],
    store: Annotated[BaseStore, InjectedStore()],
) -> str:
    """Extract proposed task changes from the conversation.

    Call this ONLY when the user mentions a discrete, one-shot actionable
    to-do (a task/reminder/plan to do something). Do NOT call for recurring
    habits or bookkeeping ("记录这个月开销", "每天量血压"), information
    queries, content-writing requests, or chit-chat — those are out of scope
    and should be handled by the capability-boundary reply instead.
    Returns a JSON payload with proposed task changes; the graph's
    ``hitl_node`` will present them for human approval before writing.
    """
    user_id = _user_id(state)
    namespace = ("task", user_id)

    existing_items = store.search(namespace)
    existing_memories = (
        [(item.key, "Task", item.value) for item in existing_items]
        if existing_items
        else None
    )

    instruction = TRUSTCALL_INSTRUCTION.format(time=datetime.now().isoformat())
    # TrustCall only needs the latest user intent + the current task list (in
    # `existing`). Feeding the whole history pollutes the extraction: stale
    # "No task changes detected." results and "[SYSTEM NOTIFICATION]" delete
    # notices (which say "Do NOT re-add it") get re-injected and make the
    # extractor produce nothing on subsequent turns.
    latest_user = next(
        (
            m
            for m in reversed(state["messages"])
            if m.type == "human" and getattr(m, "content", "").strip()
        ),
        None,
    )
    updated_messages = [SystemMessage(content=instruction)]
    if latest_user is not None:
        updated_messages.append(latest_user)

    extractor = create_extractor(
        get_model(),
        tools=[Task],
        tool_choice="Task",
        enable_inserts=True,
    )
    result = await extractor.ainvoke(
        {"messages": updated_messages, "existing": existing_memories},
        {"recursion_limit": 25},
    )

    # ── Collect proposed changes (NO interrupt, NO store write) ──
    proposed: list[dict] = []
    for response, metadata in zip(result["responses"], result["response_metadata"]):
        key = metadata.get("json_doc_id", str(uuid.uuid4()))
        action = metadata.get("json_doc_action", "insert")
        proposed.append(
            {
                "key": key,
                "action": action,
                "task": response.model_dump(mode="json"),
            }
        )

    if not proposed:
        return "No task changes detected."

    return _json.dumps(
        {
            "type": "task_proposals",
            "proposed": proposed,
            "user_id": user_id,
        },
        ensure_ascii=False,
    )


@tool
async def update_instructions(
    state: Annotated[AgentState, InjectedState()],
    store: Annotated[BaseStore, InjectedStore()],
) -> str:
    """Update user-specified planning instructions based on the conversation.

    Call this when the user describes preferences for how tasks should be
    planned, prioritized, assigned, or formatted.
    """
    user_id = _user_id(state)

    existing = get_instructions(store, user_id)
    current = existing.get("memory") if existing else None

    system_msg = CREATE_INSTRUCTIONS.format(current_instructions=current or "无")
    model = get_model()
    new_memory = await model.ainvoke(
        [SystemMessage(content=system_msg)]
        + state["messages"][:-1]
        + [SystemMessage(content="Please update the instructions based on the conversation.")]
    )

    put_instructions(store, user_id, {"memory": new_memory.content})
    return "Planning instructions updated."
