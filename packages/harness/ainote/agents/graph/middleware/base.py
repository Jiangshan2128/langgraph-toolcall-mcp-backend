"""Middleware protocol and pipeline for the agent node.

The pipeline composes middlewares via the "Russian doll" pattern:
each middleware wraps the next handler in the chain, so the first
middleware runs first on the way in and last on the way out.

Usage::

    pipeline = Pipeline(
        middlewares=[
            ErrorHandlingMiddleware(),
            MemoryLoadMiddleware(),
            SystemPromptMiddleware(),
            ToolBindingMiddleware(),
        ],
        core_handler=_llm_invoke_handler,
    )

    # In agent_node:
    return await pipeline.run(state, runtime)
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from ainote.agents.models import Configuration
from ainote.agents.graph.state import AgentState
from langgraph.runtime import Runtime

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# Mutable dict that flows through the entire middleware chain for a single
# agent_node invocation. Created fresh each time pipeline.run() is called.
MiddlewareContext = dict[str, Any]

# All handlers in the chain share this signature.
# The core_handler (innermost) also receives context so it can read
# the model and system prompt that earlier middlewares stored there.
NodeHandler = Callable[
    [AgentState, Runtime[Configuration], MiddlewareContext],
    Awaitable[dict],
]


class Middleware(Protocol):
    """A middleware wraps the next handler in the chain.

    It can:
      - Read/write ``state`` (AgentState) and ``runtime`` (LangGraph Runtime)
      - Read/write ``context`` to pass data to downstream middlewares
      - Transform or short-circuit by not calling ``next_handler``
    """

    async def __call__(
        self,
        state: AgentState,
        runtime: Runtime[Configuration],
        context: MiddlewareContext,
        next_handler: NodeHandler,
    ) -> dict: ...


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Pipeline:
    """Compose middlewares into a single callable chain.

    With middlewares ``[A, B, C]`` and core handler ``H``, produces::

        A(B(C(H)))

    i.e. A runs first on the way in, then B, then C, then H; unwinding
    in reverse order on the way out.
    """

    def __init__(
        self,
        middlewares: list[Middleware],
        core_handler: NodeHandler,
    ) -> None:
        self._middlewares = middlewares
        self._core_handler = core_handler

    async def run(
        self,
        state: AgentState,
        runtime: Runtime[Configuration],
    ) -> dict:
        """Execute the full middleware chain.

        A fresh ``context`` dict is created per invocation — the pipeline
        instance itself is stateless.
        """
        context: MiddlewareContext = {}
        chain = self._build_chain(context)
        return await chain(state, runtime, context)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_chain(self, context: MiddlewareContext) -> NodeHandler:
        """Build the nested handler chain.

        Starting from the core handler, wrap each middleware around it
        in reverse order so the first middleware in the list is outermost.
        """
        handler = self._core_handler
        for mw in reversed(self._middlewares):
            handler = self._wrap(mw, context, handler)
        return handler

    def _wrap(
        self,
        mw: Middleware,
        context: MiddlewareContext,
        next_handler: NodeHandler,
    ) -> NodeHandler:
        async def wrapped(
            state: AgentState,
            runtime: Runtime[Configuration],
            ctx: MiddlewareContext,
        ) -> dict:
            return await mw(state, runtime, ctx, next_handler)

        return wrapped
