import json as _json
import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal, Optional

from langchain.tools import tool
from langchain_core.messages import SystemMessage, merge_message_runs
from langgraph.prebuilt.tool_node import InjectedState, InjectedStore
from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field
from trustcall import create_extractor

from app.agents.models import get_model
from app.agents.prompts import CREATE_INSTRUCTIONS, TRUSTCALL_INSTRUCTION
from app.agents.graph.state import AgentState
from app.agents.memory import (
    get_instructions,
    get_profile,
    get_tasks,
    put_instructions,
    put_profile,
)


class Profile(BaseModel):
    """Profile of the user the agent is chatting with."""

    name: Optional[str] = Field(default=None, description="The user's name")
    location: Optional[str] = Field(default=None, description="The user's location")
    role: Optional[str] = Field(default=None, description="The user's role or job")
    connections: list[str] = Field(
        default_factory=list,
        description="People, teams, or groups the user works with",
    )
    preferences: list[str] = Field(
        default_factory=list,
        description="Preferences for task planning and communication",
    )


class Task(BaseModel):
    """A task the user wants to track."""

    title: str = Field(description="The task title")
    description: Optional[str] = Field(default=None, description="Task details")
    assignee: Optional[str] = Field(default=None, description="Who should own the task")
    priority: Literal["P0", "P1", "P2"] = Field(
        default="P1", description="P0 = urgent today, P1 = important, P2 = routine"
    )
    time: str = Field(default="", description="when to start the task, e.g. 'today' or 'next week'")
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
    location, role, connections, or preferences.
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
        {"messages": updated_messages, "existing": existing_memories}
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

    Call this when the user mentions any plan or tasks.
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
    updated_messages = list(
        merge_message_runs(
            messages=[SystemMessage(content=instruction)] + state["messages"][:-1]
        )
    )

    extractor = create_extractor(
        get_model(),
        tools=[Task],
        tool_choice="Task",
        enable_inserts=True,
    )
    result = await extractor.ainvoke(
        {"messages": updated_messages, "existing": existing_memories}
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
