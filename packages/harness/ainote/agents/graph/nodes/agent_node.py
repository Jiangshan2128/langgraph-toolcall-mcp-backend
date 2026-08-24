import logging

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from ainote.agents.graph.model.model import Configuration
from ainote.agents.graph.nodes.middleware import (
    MemoryLoadMiddleware,
    Pipeline,
    SystemPromptMiddleware,
    ToolBindingMiddleware,
)
from ainote.agents.graph.state import AgentState

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
# Pipeline (factory + shared singleton) — internal to agent_node
# ======================================================================
#
# The pipeline is stateless: ``Pipeline.run()`` creates a fresh ``context``
# dict and closure chain per invocation, and every middleware reads per-user
# data from ``state``/``runtime`` at call time. A single shared instance is
# therefore safe and sufficient. ``agent_node`` resolves it internally via
# ``get_pipeline()``; exposing a pure factory (plus a lazy singleton holder)
# instead of a module-load global keeps import-time construction out and lets
# tests inject a fake pipeline without ``build_graph`` knowing about it.

_pipeline: Pipeline | None = None


def create_pipeline() -> Pipeline:
    """Pure factory — no import-time side effects, no I/O.

    Order matters: outermost first, innermost last. The middlewares are pure
    "prepare" layers (load memories → build prompt → bind tools); fault
    tolerance (retry/timeout/error handling) lives at the graph level in
    ``graph/fault_tolerance.py``, not in the pipeline.
    """
    return Pipeline(
        middlewares=[
            MemoryLoadMiddleware(),
            SystemPromptMiddleware(),
            ToolBindingMiddleware(),
        ],
        core_handler=_llm_invoke_handler,
    )


def get_pipeline() -> Pipeline:
    """Return the shared pipeline, building it once on first use.

    Production keeps a single instance across all graphs/requests (the
    pipeline is stateless, see above). Tests may inject a custom one by
    patching ``get_pipeline`` or setting ``agent_node._pipeline`` directly.
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


# ======================================================================
# Public node function
# ======================================================================


async def agent_node(
    state: AgentState,
    runtime: Runtime[Configuration],
):
    """Load memories, build prompt, bind tools, and invoke the LLM.

    Implementation delegates to a middleware pipeline for separation of
    concerns. Each middleware handles one aspect of the request lifecycle:
    memory loading, system prompt construction, and tool binding. Fault
    tolerance (retry/timeout/error handling) is applied by the graph runtime
    (see ``graph/fault_tolerance.py``), not by the pipeline.

    Before the pipeline runs, per-user DingTalk tools are ensured (idempotent):
    the first turn after a restart lazily loads the user's enabled DingTalk MCP
    tools from the store. Ordering is guaranteed: ensure → SystemPrompt reads
    the per-user deferred names → ToolBinding binds the per-user tools → LLM →
    ScopedToolNode executes against the cached per-user ToolNode.
    """
    return await _run_agent(state, runtime, get_pipeline())
