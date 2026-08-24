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

from ainote.agents.graph.model.model import Configuration
from ainote.agents.graph.nodes.middleware.base import MiddlewareContext, NodeHandler
from ainote.agents.graph.state import AgentState
from ainote.tools.core.tool_search import DeferredToolSetup
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Injectable dependencies
# ---------------------------------------------------------------------------


def _default_deferred_getter(user_id: str) -> DeferredToolSetup | None:
    from ainote.agents.graph.dingtalk_runtime import get_user_deferred_setup

    return get_user_deferred_setup(user_id)


def _default_template() -> str:
    from ainote.agents.graph.prompts import MODEL_SYSTEM_MESSAGE

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
        *deferred_setup_getter* — ``(user_id: str) -> DeferredToolSetup | None``
        *template* — ``str`` (the prompt template with ``{...}`` placeholders)
    """

    def __init__(
        self,
        deferred_setup_getter: Callable[[str], DeferredToolSetup | None] | None = None,
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
        from ainote.tools.core.tool_search import get_deferred_tools_prompt_section

        profile = context.get("profile")
        tasks: list[dict] = context.get("tasks", [])
        instructions = context.get("instructions", {})

        user_id = state.get("user_id") or runtime.context.user_id
        deferred_setup = self._get_deferred(user_id)
        deferred_names = deferred_setup.deferred_names if deferred_setup else frozenset()
        deferred_section = get_deferred_tools_prompt_section(deferred_names)

        # Enrich the profile with the user's DingTalk union_id if available.
        # DingTalk todo tools (dingtalk_queryTasks etc.) require it, and it's
        # only obtained at OAuth time (stored in the dingtalk token), NOT in
        # the profile itself. Injecting it lets the LLM call DingTalk tools
        # directly instead of searching contacts (which the app may lack
        # permission for) or asking the user.
        profile = context.get("profile") or {}
        if profile:
            profile = dict(profile)
            from ainote.agents.graph.memory import get_dingtalk_token

            token = get_dingtalk_token(runtime.store, user_id)
            # isinstance guard: tests pass MagicMock stores; only inject a real value.
            if isinstance(token, dict) and token.get("union_id"):
                profile.setdefault("dingtalk_union_id", token["union_id"])

        context["system_message"] = self._template.format(
            user_profile=profile or "未设置",
            tasks="\n".join(str(task) for task in tasks) or "无",
            instructions=instructions.get("memory", "") if instructions else "无",
            deferred_tools=deferred_section,
        )

        logger.debug("System prompt built (deferred tools: %d)", len(deferred_names))
        return await next_handler(state, runtime, context)
