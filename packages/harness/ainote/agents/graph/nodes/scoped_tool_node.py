"""Per-user-scoped ToolNode.

The graph's ``tools`` node is a single ``ScopedToolNode`` holding ONLY the
shared core tools. At each invocation it resolves the current user from
``runtime.context.user_id`` (falling back to graph state) and delegates to
that user's CACHED ``ToolNode`` (core + the user's enabled DingTalk tools),
built once at enable/load time.

This keeps DingTalk tools out of the shared ``ALL_TOOLS`` / graph entirely —
user A's enable never leaks into user B's sessions, and there is no shared
``tools_by_name`` mutation to race on (each user has their own node instance).

Design notes (verified against langgraph source):
- ``ToolNode.__init__`` passes ``self._func``/``self._afunc`` to
  ``RunnableCallable``, which inspects the overridden signature
  ``(self, input, config, runtime)`` and injects ``config`` + ``runtime``
  when Pregel calls the node.
- Delegating to a plain ``ToolNode`` instance preserves all behavior:
  Command outputs, ``asyncio.gather`` parallelism, InjectedState/InjectedStore
  injection, and error handling all live inside the inner node.
- The sync ``_func`` cannot lazy-load (that spawns a subprocess, async only).
  This app only uses ``ainvoke``/``astream_events`` (async), and
  ``agent_node`` already awaits ``ensure_user_tools`` before the pipeline, so
  the async path is the hot path; the sync path degrades to core-only.
"""

from __future__ import annotations

from typing import Any, Union

from langchain_core.messages import AnyMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from pydantic import BaseModel

# Must mirror ToolNode._func/_afunc's input union so RunnableCallable's
# signature introspection injects config/runtime without warnings.
_ToolInput = Union[list[AnyMessage], dict[str, Any], BaseModel]


class ScopedToolNode(ToolNode):
    """ToolNode that routes tool execution to the current user's tool set."""

    def __init__(self, tools, *, name: str = "tools", **kwargs):
        super().__init__(tools, name=name, **kwargs)
        self._core_tools = list(tools)

    def _resolve_user_id(
        self, input: _ToolInput, config: RunnableConfig, runtime: Runtime
    ) -> str:
        """Resolve the current user id.

        ``runtime.context.user_id`` is the primary source (always populated —
        the graph is compiled with ``context_schema=Configuration`` and chat
        passes ``context=Configuration(user_id=...)``). Graph state is the
        fallback for non-standard input shapes.
        """
        ctx = getattr(runtime, "context", None)
        uid = getattr(ctx, "user_id", None)
        if uid in (None, ""):
            state = self._extract_state(input, config)
            uid = state.get("user_id") if isinstance(state, dict) else None
        return uid or "default"

    def _user_node(self, user_id: str):
        """Return the user's cached ToolNode, or None when not enabled.

        Lazy import breaks the module cycle
        builder → scoped_tool_node → dingtalk_runtime → builder.
        """
        from ainote.agents.graph.dingtalk_runtime import get_user_tool_node

        return get_user_tool_node(user_id)

    def _func(
        self, input: _ToolInput, config: RunnableConfig, runtime: Runtime
    ) -> Any:
        node = self._user_node(self._resolve_user_id(input, config, runtime))
        if node is None:
            return super()._func(input, config, runtime)
        return node._func(input, config, runtime)

    async def _afunc(
        self, input: _ToolInput, config: RunnableConfig, runtime: Runtime
    ) -> Any:
        node = self._user_node(self._resolve_user_id(input, config, runtime))
        if node is None:
            return await super()._afunc(input, config, runtime)
        return await node._afunc(input, config, runtime)
