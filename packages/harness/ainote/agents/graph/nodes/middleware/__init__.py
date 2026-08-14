"""Middleware pipeline for the agent node.

Each middleware handles one aspect of the agent lifecycle: memory loading,
system prompt construction, tool binding, and error handling. The pipeline
composes them via the Russian-doll pattern so cross-cutting concerns are
separated from the core LLM invocation.
"""

from ainote.agents.graph.nodes.middleware.base import (
    Middleware,
    MiddlewareContext,
    NodeHandler,
    Pipeline,
)
from ainote.agents.graph.nodes.middleware.error_handler import ErrorHandlingMiddleware
from ainote.agents.graph.nodes.middleware.memory_load import MemoryLoadMiddleware
from ainote.agents.graph.nodes.middleware.system_prompt import SystemPromptMiddleware
from ainote.agents.graph.nodes.middleware.tool_binding import ToolBindingMiddleware

__all__ = [
    "ErrorHandlingMiddleware",
    "MemoryLoadMiddleware",
    "Middleware",
    "MiddlewareContext",
    "NodeHandler",
    "Pipeline",
    "SystemPromptMiddleware",
    "ToolBindingMiddleware",
]
