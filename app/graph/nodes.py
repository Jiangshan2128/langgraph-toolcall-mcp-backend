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
from app.graph.deferred_cache import (
    get_deferred_setup_cached,
    refresh_deferred_setup,
)
from app.graph.middleware import (
    ErrorHandlingMiddleware,
    MemoryLoadMiddleware,
    Pipeline,
    SystemPromptMiddleware,
    ToolBindingMiddleware,
)
from app.graph.state import AgentState
from app.store.memory import (
    delete_task as _delete_task,
    put_tasks,
)

logger = logging.getLogger(__name__)


# ======================================================================
# Core handler (private)
# ======================================================================


async def _llm_invoke_handler(state, runtime, context):
    """Core handler: invoke the LLM with the prepared model and system prompt.

    Context keys read:
        ``"system_message"`` — ``str`` (from ``SystemPromptMiddleware``)
        ``"model"``          — ``ChatOpenAI`` (from ``ToolBindingMiddleware``)
    """
    model = context["model"]
    system_msg = SystemMessage(content=context["system_message"])
    response = await model.ainvoke([system_msg] + state["messages"])
    return {"messages": [response]}


# ======================================================================
# Pipeline (built once at module load)
# ======================================================================

_agent_pipeline = Pipeline(
    middlewares=[
        # Order matters: outermost first, innermost last.
        # Error handling MUST be outermost to catch everything.
        ErrorHandlingMiddleware(),
        MemoryLoadMiddleware(),
        SystemPromptMiddleware(),
        ToolBindingMiddleware(),
    ],
    core_handler=_llm_invoke_handler,
)


# ======================================================================
# Public node functions
# ======================================================================


async def agent_node(
    state: AgentState,
    runtime: Runtime[Configuration],
):
    """Load memories, build prompt, bind tools, and invoke the LLM.

    Implementation delegates to a middleware pipeline for separation of
    concerns. Each middleware handles one aspect of the request lifecycle:
    error handling, memory loading, system prompt construction, and tool
    binding.
    """
    return await _agent_pipeline.run(state, runtime)


# ======================================================================
# HITL (unchanged)
# ======================================================================


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

    # ── Build return messages ──
    messages: list[HumanMessage] = [HumanMessage(content=summary)]

    # Check if user wants to submit tasks to DingTalk
    if approval.get("submit_to_dingtalk", False):
        DINGTALK_TASK_MANAGEMENT_TEMPLATE = """
Based on the task changes below, you should sync relevant tasks to DingTalk to keep the user's DingTalk task list in sync.

Here is the summary of task changes:
{summary}
"""
        mcpPrompt = DINGTALK_TASK_MANAGEMENT_TEMPLATE.format(summary=summary)
        messages.append(HumanMessage(content=mcpPrompt))

    return {"messages": messages}