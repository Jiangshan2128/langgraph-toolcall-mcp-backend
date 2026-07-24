"""Bind core + deferred + promoted tools to the ChatOpenAI model.

Context keys read:  (none — reads ``state["promoted_tools"]`` directly)

Context keys written:
    ``"model"`` — ``ChatOpenAI`` (model with tools bound)
"""

from __future__ import annotations

import logging
from typing import Callable

from app.agents.models import Configuration
from app.agents.graph.middleware.base import MiddlewareContext, NodeHandler
from app.agents.graph.state import AgentState
from langchain_openai import ChatOpenAI
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Injectable dependency
# ---------------------------------------------------------------------------


def _default_model_binder(*, promoted_names: list[str] | None = None) -> ChatOpenAI:
    from app.agents.graph.tool_router import get_model_with_tools

    return get_model_with_tools(promoted_names=promoted_names)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class ToolBindingMiddleware:
    """Bind tools to the ChatOpenAI model.

    Calls ``get_model_with_tools()`` with any promoted DingTalk MCP tool
    names from ``state["promoted_tools"]``. The bound model instance is
    stored in ``context["model"]`` for the core handler.

    Dependencies (injectable for testing):
        *model_binder* — ``(**kw) -> ChatOpenAI``
    """

    def __init__(
        self,
        model_binder: Callable[..., ChatOpenAI] | None = None,
    ) -> None:
        self._bind = model_binder or _default_model_binder

    async def __call__(
        self,
        state: AgentState,
        runtime: Runtime[Configuration],
        context: MiddlewareContext,
        next_handler: NodeHandler,
    ) -> dict:
        promoted = state.get("promoted_tools")
        context["model"] = self._bind(promoted_names=promoted)

        logger.debug("Model bound with %d promoted tools", len(promoted or []))
        return await next_handler(state, runtime, context)
