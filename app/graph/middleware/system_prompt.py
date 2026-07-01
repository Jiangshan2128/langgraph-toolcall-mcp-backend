"""Build the system prompt from memories and deferred-tool info.

Context keys read:
    ``"profile"``      — ``dict | None`` (from ``MemoryLoadMiddleware``)
    ``"tasks"``        — ``list[dict]`` (from ``MemoryLoadMiddleware``)
    ``"instructions"`` — ``dict | None`` (from ``MemoryLoadMiddleware``)

Context keys written:
    ``"system_message"`` — ``str`` (the fully formatted system prompt)
"""

from __future__ import annotations

import logging
from typing import Callable

from app.agents.config import Configuration
from app.graph.middleware.base import MiddlewareContext, NodeHandler
from app.graph.state import AgentState
from app.tools.tool_search import DeferredToolSetup
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Injectable dependencies
# ---------------------------------------------------------------------------


def _default_deferred_getter() -> DeferredToolSetup | None:
    from app.graph.deferred_cache import get_deferred_setup_cached

    return get_deferred_setup_cached()


def _default_template() -> str:
    from app.agents.config import MODEL_SYSTEM_MESSAGE

    return MODEL_SYSTEM_MESSAGE


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class SystemPromptMiddleware:
    """Build the system prompt from loaded memories.

    Formats ``MODEL_SYSTEM_MESSAGE`` with user profile, tasks, instructions,
    and the deferred-tools prompt section (DingTalk MCP tools available
    via ``tool_search``).

    Dependencies (injectable for testing):
        *deferred_setup_getter* — ``() -> DeferredToolSetup | None``
        *template* — ``str`` (the prompt template with ``{...}`` placeholders)
    """

    def __init__(
        self,
        deferred_setup_getter: Callable[[], DeferredToolSetup | None] | None = None,
        template: str | None = None,
    ) -> None:
        self._get_deferred = deferred_setup_getter or _default_deferred_getter
        self._template = template or _default_template()

    async def __call__(
        self,
        state: AgentState,
        runtime: Runtime[Configuration],
        context: MiddlewareContext,
        next_handler: NodeHandler,
    ) -> dict:
        from app.tools.tool_search import get_deferred_tools_prompt_section

        profile = context.get("profile")
        tasks: list[dict] = context.get("tasks", [])
        instructions = context.get("instructions", {})

        deferred_setup = self._get_deferred()
        deferred_names = deferred_setup.deferred_names if deferred_setup else frozenset()
        deferred_section = get_deferred_tools_prompt_section(deferred_names)

        context["system_message"] = self._template.format(
            user_profile=profile or "未设置",
            tasks="\n".join(str(task) for task in tasks) or "无",
            instructions=instructions.get("memory", "") if instructions else "无",
            deferred_tools=deferred_section,
        )

        logger.debug("System prompt built (deferred tools: %d)", len(deferred_names))
        return await next_handler(state, runtime, context)
