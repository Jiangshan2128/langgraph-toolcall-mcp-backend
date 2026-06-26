import json
import logging

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage, HumanMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.agents.config import (
    MODEL_SYSTEM_MESSAGE,
    Configuration,
)
from app.core.debug_utils import (
    build_hitl_summary,
    print_final_upserts,
    print_proposed_tasks,
    print_approval_result,
)
from app.graph.state import AgentState
from app.graph.tool_router import get_model_with_tools
from app.store.memory import (
    get_instructions,
    get_profile,
    get_tasks,
    put_tasks,
    delete_task as _delete_task,
)

logger = logging.getLogger(__name__)


async def agent_node(
    state: AgentState,
    runtime: Runtime[Configuration],
):
    """Load memories and decide which tools to call, if any."""
    user_id = state.get("user_id") or runtime.context.user_id

    profile = get_profile(runtime.store, user_id)
    tasks = get_tasks(runtime.store, user_id)
    instructions = get_instructions(runtime.store, user_id)

    system_msg = MODEL_SYSTEM_MESSAGE.format(
        user_profile=profile or "未设置",
        tasks="\n".join(str(task) for task in tasks) or "无",
        instructions=instructions.get("memory", "") if instructions else "无",
    )

    model = await get_model_with_tools(state["messages"])
    try:
        response = await model.ainvoke(
            [SystemMessage(content=system_msg)] + state["messages"]
        )
    except Exception as exc:
        logger.exception("agent_node model.ainvoke failed for user=%s", user_id)
        error_msg = (
            "抱歉，模型服务暂时不可用，请稍后重试。"
            if "BadRequestError" in type(exc).__name__ or "400" in str(exc)
            else f"An error occurred: {exc}"
        )
        return {"messages": [AIMessage(content=error_msg)]}

    return {"messages": [response]}


def _parse_task_proposals(state: AgentState) -> list[dict] | None:
    """Scan messages for the last update_tasks tool output and parse proposals."""
    for m in reversed(state["messages"]):
        if isinstance(m, ToolMessage) and m.name == "update_tasks":
            try:
                payload = json.loads(m.content)
                if isinstance(payload, dict) and payload.get("type") == "task_proposals":
                    return payload.get("proposed")
            except (json.JSONDecodeError, TypeError):
                continue
    return None


async def hitl_node(
    state: AgentState,
    runtime: Runtime[Configuration],
):
    """Human-in-the-loop node: present task proposals for approval.

    This node runs *after* ``update_tasks`` tool completes. It:
    1. Reads the proposals from the tool's ``ToolMessage`` in ``state["messages"]``.
    2. Calls ``interrupt()`` to pause for human approval.
    3. On resume, applies approved/edited changes to the store.

    On resume the ``tools`` node is NOT re-executed — only this node
    restarts.  The proposals with their original keys are recovered from
    the checkpointed ``ToolMessage``, so frontend ``edited_tasks`` keys
    always match.
    """
    user_id = state.get("user_id") or runtime.context.user_id

    # ── Read proposals ──
    # On first run: parse from the tool's ToolMessage in state["messages"].
    # On resume: ToolMessage is still in state["messages"] (checkpointed)
    # because the "tools" node does NOT re-execute — only "hitl_node" restarts.
    proposed = _parse_task_proposals(state)
    
    # DEBUG: Print proposed tasks
    print_proposed_tasks(proposed)
    
    if not proposed:
        logger.warning("hitl_node: no task proposals found for user=%s", user_id)
        return {
            "messages": [AIMessage(content="No task changes to review.")],
        }

    logger.info("HITL interrupt — %d proposed task change(s) for user=%s", len(proposed), user_id)

    interrupt_payload = {
        "type": "task_update_approval",
        "message": f"The agent wants to apply {len(proposed)} task change(s). "
        f"Review and approve, edit, or reject each one.",
        "proposed_updates": proposed,
    }
    approval: dict = interrupt(interrupt_payload)

    if not approval.get("approved", False):
        logger.info("Task updates rejected by user=%s (%d change(s))", user_id, len(proposed))
        return {
            "messages": [AIMessage(content=f"Task updates rejected ({len(proposed)} change(s) discarded).")],
        }

    # ── Apply approved changes ──
    rejected_keys: set[str] = set(approval.get("rejected_keys", []))
    edited_tasks: dict[str, dict] = {
        e["key"]: e["task"] for e in approval.get("edited_tasks", [])
    }
    
    # DEBUG: Print approval details and edited tasks
    print_approval_result(approval, rejected_keys, edited_tasks)

    upserts: list[tuple[str, dict]] = []
    deleted_count = 0
    for p in proposed:
        key = p["key"]
        if key in rejected_keys:
            continue
        if p["action"] == "delete":
            _delete_task(runtime.store, user_id, key)
            deleted_count += 1
            continue
        task_data = edited_tasks.get(key, p["task"])
        upserts.append((key, task_data))

    if upserts:
        print_final_upserts(upserts)
        put_tasks(runtime.store, user_id, upserts)
    else:
        print_final_upserts([])

    logger.info(
        "HITL approved — %d upsert(s), %d delete(s) for user=%s",
        len(upserts), deleted_count, user_id,
    )

    # ── Build detailed summary message ──
    summary = build_hitl_summary(
        proposed=proposed,
        edited_tasks=edited_tasks,
        rejected_keys=rejected_keys,
        deleted_count=deleted_count,
        upserts_count=len(upserts),
    )

    return {
        "messages": [HumanMessage(content=summary)],
    }