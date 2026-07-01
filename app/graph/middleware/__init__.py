"""Middleware pipeline for the agent node.

Each middleware handles one aspect of the agent lifecycle: memory loading,
system prompt construction, tool binding, and error handling. The pipeline
composes them via the Russian-doll pattern so cross-cutting concerns are
separated from the core LLM invocation.
"""

from app.graph.middleware.base import (
    Middleware,
    MiddlewareContext,
    NodeHandler,
    Pipeline,
)
from app.graph.middleware.error_handler import ErrorHandlingMiddleware
from app.graph.middleware.memory_load import MemoryLoadMiddleware
from app.graph.middleware.system_prompt import SystemPromptMiddleware
from app.graph.middleware.tool_binding import ToolBindingMiddleware

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
