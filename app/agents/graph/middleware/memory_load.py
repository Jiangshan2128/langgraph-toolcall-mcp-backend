"""Load user memories (profile, tasks, instructions) from the store.

Context keys written:
    ``"profile"``      — ``dict | None`` (the user profile)
    ``"tasks"``        — ``list[dict]`` (task list with keys injected)
    ``"instructions"`` — ``dict | None`` (stored instructions)
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from app.agents.models import Configuration
from app.agents.graph.middleware.base import MiddlewareContext, NodeHandler
from app.agents.graph.state import AgentState
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Injectable dependency
# ---------------------------------------------------------------------------


class StoreAccessor(Protocol):
    """Abstracts the three memory-loading calls so the middleware is testable."""

    def get_profile(self, store: Any, user_id: str) -> dict | None: ...

    def get_tasks(self, store: Any, user_id: str) -> list[dict]: ...

    def get_instructions(self, store: Any, user_id: str) -> dict | None: ...


class _RealStoreAccessor:
    """Production implementation — delegates to ``app.store.memory``."""

    def get_profile(self, store: Any, user_id: str) -> dict | None:
        from app.agents.memory import get_profile

        return get_profile(store, user_id)

    def get_tasks(self, store: Any, user_id: str) -> list[dict]:
        from app.agents.memory import get_tasks

        return get_tasks(store, user_id)

    def get_instructions(self, store: Any, user_id: str) -> dict | None:
        from app.agents.memory import get_instructions

        return get_instructions(store, user_id)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class MemoryLoadMiddleware:
    """Load profile, tasks, and instructions from the LangGraph store.

    Reads ``user_id`` from ``state`` (with fallback to ``runtime.context``)
    and loads all three memory namespaces into the middleware context dict.

    On failure: lets the exception propagate (caught by
    ``ErrorHandlingMiddleware`` if present, otherwise by the graph runtime).
    """

    def __init__(self, store_accessor: StoreAccessor | None = None) -> None:
        if store_accessor is None:
            store_accessor = _RealStoreAccessor()
        self._get_profile = store_accessor.get_profile
        self._get_tasks = store_accessor.get_tasks
        self._get_instructions = store_accessor.get_instructions

    async def __call__(
        self,
        state: AgentState,
        runtime: Runtime[Configuration],
        context: MiddlewareContext,
        next_handler: NodeHandler,
    ) -> dict:
        user_id: str = state.get("user_id") or runtime.context.user_id  # type: ignore[assignment]

        context["profile"] = self._get_profile(runtime.store, user_id)
        context["tasks"] = self._get_tasks(runtime.store, user_id)
        context["instructions"] = self._get_instructions(runtime.store, user_id)

        logger.debug("Memories loaded for user=%s", user_id)
        return await next_handler(state, runtime, context)
