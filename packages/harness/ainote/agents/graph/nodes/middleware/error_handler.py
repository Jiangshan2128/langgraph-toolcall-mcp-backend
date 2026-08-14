"""Catch exceptions from the entire downstream middleware chain.

Place this as the **outermost** middleware so it wraps everything:
memory loading, prompt construction, tool binding, and the LLM call.

Context keys read / written:  (none)
"""

from __future__ import annotations

import logging

from ainote.agents.models import Configuration
from ainote.agents.graph.nodes.middleware.base import MiddlewareContext, NodeHandler
from ainote.agents.graph.state import AgentState
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


class ErrorHandlingMiddleware:
    """Catch exceptions from the entire downstream chain.

    On ``BadRequestError`` / HTTP 400:
        Returns a Chinese-language apology message.
    On any other exception:
        Returns the exception message inline.
    """

    async def __call__(
        self,
        state: AgentState,
        runtime: Runtime[Configuration],
        context: MiddlewareContext,
        next_handler: NodeHandler,
    ) -> dict:
        try:
            return await next_handler(state, runtime, context)
        except Exception as exc:
            user_id = state.get("user_id") or runtime.context.user_id
            logger.exception("agent_node pipeline failed for user=%s", user_id)

            exc_type_name = type(exc).__name__
            is_bad_request = "BadRequestError" in exc_type_name or "400" in str(exc)

            error_msg = (
                "抱歉，模型服务暂时不可用，请稍后重试。"
                if is_bad_request
                else f"An error occurred: {exc}"
            )
            return {"messages": [AIMessage(content=error_msg)]}
