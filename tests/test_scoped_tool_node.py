"""Tests for ScopedToolNode (per-user tool routing at the graph's tools node).

Verifies:
- user_id resolution (runtime.context primary, graph state fallback)
- core-only delegation when the user has no DingTalk runtime
- delegation to the user's cached ToolNode when enabled
- command/tool outputs pass through the delegation
"""

import types

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from ainote.agents.graph.nodes.scoped_tool_node import ScopedToolNode


@tool
def ping() -> str:
    """A test tool."""
    return "pong"


@tool
def dingtalk_ping() -> str:
    """A fake DingTalk MCP tool (per-user)."""
    return "ding-pong"


def _runtime(user_id: str):
    return types.SimpleNamespace(
        context=types.SimpleNamespace(user_id=user_id),
        store=types.SimpleNamespace(),  # minimal; ping needs no store
        stream_writer=None,
        execution_info=None,
        server_info=None,
    )


def _call_input():
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ping",
                        "args": {},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }


def _dingtalk_input():
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "dingtalk_ping",
                        "args": {},
                        "id": "call-2",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }


def _config():
    return {"configurable": {}}


# ── user_id resolution ──────────────────────────────────────────────────


def test_resolve_user_id_prefers_runtime_context():
    node = ScopedToolNode([ping])
    assert node._resolve_user_id({}, _config(), _runtime("runtime-user")) == "runtime-user"


def test_resolve_user_id_falls_back_to_state():
    node = ScopedToolNode([ping])
    runtime = types.SimpleNamespace(context=types.SimpleNamespace(user_id=""))
    state = {"user_id": "state-user"}
    assert node._resolve_user_id(state, _config(), runtime) == "state-user"


def test_resolve_user_id_defaults_to_default():
    node = ScopedToolNode([ping])
    runtime = types.SimpleNamespace(context=types.SimpleNamespace(user_id=""))
    assert node._resolve_user_id({}, _config(), runtime) == "default"


# ── core-only path (user has no DingTalk runtime) ───────────────────────


def test_core_tool_executes_when_no_user_runtime(monkeypatch):
    """Unknown user → delegate to super() (core tools only)."""
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.get_user_tool_node", lambda uid: None
    )
    node = ScopedToolNode([ping])

    result = node._func(_call_input(), _config(), _runtime("nobody"))

    msgs = result["messages"]
    assert msgs[-1].content == "pong"


async def test_afunc_core_tool_executes_when_no_user_runtime(monkeypatch):
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.get_user_tool_node", lambda uid: None
    )
    node = ScopedToolNode([ping])

    result = await node._afunc(_call_input(), _config(), _runtime("nobody"))

    msgs = result["messages"]
    assert msgs[-1].content == "pong"


# ── delegation to user's cached node ────────────────────────────────────


def test_delegates_to_user_tool_node(monkeypatch):
    """Enabled user → delegate to their cached ToolNode (core + DingTalk)."""
    user_node = ToolNode([ping, dingtalk_ping])
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.get_user_tool_node",
        lambda uid: user_node,
    )
    node = ScopedToolNode([ping])

    # Core tool still works via the user node
    result = node._func(_call_input(), _config(), _runtime("user-1"))
    assert result["messages"][-1].content == "pong"

    # DingTalk tool works too (only available via the user's node)
    result2 = node._func(_dingtalk_input(), _config(), _runtime("user-1"))
    assert result2["messages"][-1].content == "ding-pong"


async def test_afunc_delegates_to_user_tool_node(monkeypatch):
    user_node = ToolNode([ping, dingtalk_ping])
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.get_user_tool_node",
        lambda uid: user_node,
    )
    node = ScopedToolNode([ping])

    result = await node._afunc(_dingtalk_input(), _config(), _runtime("user-1"))
    assert result["messages"][-1].content == "ding-pong"
