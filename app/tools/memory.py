import logging
import uuid
from datetime import datetime
from typing import Annotated

from langchain.tools import tool
from langchain_core.messages import SystemMessage, merge_message_runs
from langgraph.prebuilt.tool_node import InjectedState, InjectedStore
from langgraph.store.base import BaseStore
from langgraph.types import interrupt
from trustcall import create_extractor

from app.agents.config import (
    CREATE_INSTRUCTIONS,
    TRUSTCALL_INSTRUCTION,
    get_model,
)
from app.graph.state import AgentState
from app.schemas.domain import Profile, Task
from app.store.memory import (
    get_instructions,
    get_profile,
    get_tasks,
    put_instructions,
    put_profile,
    put_tasks,
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
    """Update the user's task list memory based on the conversation.

    Call this when the user mentions any plan or tasks.
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

    # ── Collect proposed changes BEFORE writing ──
    proposed: list[dict] = []
    for response, metadata in zip(result["responses"], result["response_metadata"]):
        key = metadata.get("json_doc_id", str(uuid.uuid4()))
        action = metadata.get("json_doc_action", "insert")  # insert | update | delete
        proposed.append(
            {
                "key": key,
                "action": action,
                "task": response.model_dump(mode="json"),
            }
        )

    if not proposed:
        return "No task changes detected."

    # ── HUMAN-IN-THE-LOOP: pause and ask for approval ──
    logger.info("HITL interrupt — %d proposed task change(s) for user=%s", len(proposed), user_id)
    approval: dict = interrupt(
        {
            "type": "task_update_approval",
            "message": f"The agent wants to apply {len(proposed)} task change(s). "
            f"Review and approve, edit, or reject each one.",
            "proposed_updates": proposed,
        }
    )

    if not approval.get("approved", False):
        return f"Task updates rejected by the user ({len(proposed)} change(s) discarded)."

    # ── Apply approved changes ──
    rejected_keys: set[str] = set(approval.get("rejected_keys", []))
    edited_tasks: dict[str, dict] = {
        e["key"]: e["task"] for e in approval.get("edited_tasks", [])
    }

    upserts: list[tuple[str, dict]] = []
    deleted_count = 0
    for p in proposed:
        key = p["key"]
        if key in rejected_keys:
            continue
        if p["action"] == "delete":
            from app.store.memory import delete_task as _del
            _del(store, user_id, key)
            deleted_count += 1
            continue
        task_data = edited_tasks.get(key, p["task"])
        upserts.append((key, task_data))

    if not upserts and deleted_count == 0:
        return "All proposed task changes were rejected or edited away."

    if upserts:
        put_tasks(store, user_id, upserts)
    logger.info(
        "HITL approved — %d upsert(s), %d delete(s) for user=%s",
        len(upserts), deleted_count, user_id,
    )
    return (
        f"Task memory updated ({len(upserts)} upsert(s), {deleted_count} delete(s)) "
        f"after human approval."
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
