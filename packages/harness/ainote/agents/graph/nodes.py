import json
import logging

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage, HumanMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from ainote.agents.models import Configuration
from ainote.agents.debug_utils import (
    build_hitl_summary,
    print_final_upserts,
    print_proposed_tasks,
    print_approval_result,
)
from ainote.agents.graph.middleware import (
    ErrorHandlingMiddleware,
    MemoryLoadMiddleware,
    Pipeline,
    SystemPromptMiddleware,
    ToolBindingMiddleware,
)
from ainote.agents.graph.state import AgentState
from ainote.agents.memory import (
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
# Pipeline (factory + shared singleton)
# ======================================================================
#
# The pipeline is stateless: ``Pipeline.run()`` creates a fresh ``context``
# dict and closure chain per invocation, and every middleware reads per-user
# data from ``state``/``runtime`` at call time. A single shared instance is
# therefore safe and sufficient. We expose a pure factory (plus a lazy
# singleton holder) instead of a module-load global so the container / tests
# can inject an explicit pipeline — mirroring ``builder.create_runtime()`` /
# ``build_graph()``.

_pipeline: Pipeline | None = None


def create_pipeline() -> Pipeline:
    """Pure factory — no import-time side effects, no I/O.

    Order matters: outermost first, innermost last. Error handling MUST be
    outermost to catch everything.
    """
    return Pipeline(
        middlewares=[
            ErrorHandlingMiddleware(),
            MemoryLoadMiddleware(),
            SystemPromptMiddleware(),
            ToolBindingMiddleware(),
        ],
        core_handler=_llm_invoke_handler,
    )


def get_pipeline() -> Pipeline:
    """Return the shared pipeline, building it once on first use.

    Production keeps a single instance across all graphs/requests (the
    pipeline is stateless, see above). Tests may inject a custom one via
    ``make_agent_node`` or by setting ``nodes._pipeline`` directly.
    """
    global _pipeline
    if _pipeline is None:
        _pipeline = create_pipeline()
    return _pipeline


async def _run_agent(
    state: AgentState,
    runtime: Runtime[Configuration],
    pipeline: Pipeline,
) -> dict:
    """Shared agent-node body: ensure DingTalk tools, then run the pipeline."""
    user_id = state.get("user_id") or runtime.context.user_id
    # Lazy import: builder → nodes → dingtalk_runtime → builder would be a
    # module-load cycle; dingtalk_runtime pulls in builder at import time.
    from ainote.agents.graph.dingtalk_runtime import ensure_user_tools

    await ensure_user_tools(user_id)
    return await pipeline.run(state, runtime)


def make_agent_node(pipeline: Pipeline | None = None):
    """Build the LangGraph ``agent`` node bound to a specific pipeline.

    ``pipeline=None`` (default) binds the shared singleton — the production
    behavior. Passing a pipeline lets tests / the container inject a fake or
    customized chain without patching module globals.
    """
    if pipeline is None:
        pipeline = get_pipeline()

    async def node(
        state: AgentState,
        runtime: Runtime[Configuration],
    ) -> dict:
        return await _run_agent(state, runtime, pipeline)

    return node


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

    Before the pipeline runs, per-user DingTalk tools are ensured (idempotent):
    the first turn after a restart lazily loads the user's enabled DingTalk MCP
    tools from the store. Ordering is guaranteed: ensure → SystemPrompt reads
    the per-user deferred names → ToolBinding binds the per-user tools → LLM →
    ScopedToolNode executes against the cached per-user ToolNode.
    """
    return await _run_agent(state, runtime, get_pipeline())


# ======================================================================
# HITL (unchanged)
# ======================================================================

DINGTALK_TASK_MANAGEMENT_TEMPLATE = """
Based on the task changes below, you should sync relevant tasks to DingTalk to keep the user's DingTalk task list in sync.

Here is the summary of task changes:
{summary}
"""


def _build_dingtalk_sync_text(selected: list[dict], edited_tasks: dict) -> str:
    """List only the DingTalk-selected tasks (with final titles/priority).

    ``selected`` is the slice of ``proposed`` whose keys were flagged for
    DingTalk; ``edited_tasks`` carries the user-edited final task data, falling
    back to the original proposal when a task wasn't edited. Empty selection
    yields an empty prompt (nothing to sync).
    """
    lines = []
    for p in selected:
        task = edited_tasks.get(p["key"], p["task"])
        title = task.get("title")
        priority = task.get("priority")
        lines.append(f'- "{title}" (priority {priority})')
    if not lines:
        return ""
    return "Create the following DingTalk todos:\n" + "\n".join(lines)


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

    # DingTalk-selected tasks (per-task pills in the approval sheet). Legacy
    # clients send a whole-form `submit_to_dingtalk` bool instead — fall back
    # to every accepted task so old behavior is preserved.
    dingtalk_keys: set[str] = set(approval.get("dingtalk_keys", []))
    if not dingtalk_keys and approval.get("submit_to_dingtalk", False):
        dingtalk_keys = {p["key"] for p in proposed if p["key"] not in rejected_keys}

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

    # Sync only the DingTalk-selected tasks to DingTalk. The prompt lists each
    # selected task with its final (user-edited) title and priority, so the LLM
    # creates exactly the todos the user flagged in the sheet.
    if dingtalk_keys:
        ding_selected = [p for p in proposed if p["key"] in dingtalk_keys]
        mcpPrompt = DINGTALK_TASK_MANAGEMENT_TEMPLATE.format(
            summary=_build_dingtalk_sync_text(ding_selected, edited_tasks)
        )
        messages.append(HumanMessage(content=mcpPrompt))

    return {"messages": messages}