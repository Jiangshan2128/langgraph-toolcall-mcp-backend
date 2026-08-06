"""Tests for the DingTalk MCP runtime toggle (dingtalk_runtime.py).

These patch ``load_mcp_tools`` / graph rebuild so no real network or npx
subprocess is ever started. A global-state fixture snapshots and restores
ALL_TOOLS / MCP_TOOL_NAMES / deferred setup / graph after each test.
"""

import pytest

from ainote.agents.graph import builder
from ainote.agents.graph.deferred_cache import (
    get_deferred_setup_cached,
    refresh_deferred_setup,
)
from ainote.agents.graph.dingtalk_runtime import (
    DingTalkError,
    _enabled,
    _last_error,
    _loaded_tool_names,
    disable_dingtalk,
    enable_dingtalk,
    get_status,
)
from ainote.tools import ALL_TOOLS
from ainote.tools.tool_search import MCP_TOOL_NAMES, register_mcp_tools


class _FakeTool:
    def __init__(self, name):
        self.name = name


def _fake_tool(name):
    t = _FakeTool(name)
    t.description = "fake"
    return t


def _make_fake_load(tool_names):
    """Build a load_mcp_tools fake that also registers tools (like the real one)."""

    async def fake_load(include=None, exclude=None):
        tools = [_fake_tool(n) for n in tool_names]
        register_mcp_tools(tools)
        return tools

    return fake_load


@pytest.fixture(autouse=True)
def _isolate_globals():
    """Snapshot the shared globals and restore them after every test."""
    snap = (
        list(ALL_TOOLS),
        set(MCP_TOOL_NAMES),
        get_deferred_setup_cached(),
        builder.graph,
        _enabled,
        set(_loaded_tool_names),
        _last_error,
    )
    yield
    ALL_TOOLS[:] = snap[0]
    MCP_TOOL_NAMES.clear()
    MCP_TOOL_NAMES.update(snap[1])
    refresh_deferred_setup(snap[2])
    builder.graph = snap[3]
    # Reset the runtime module globals too (enable/disable mutate them).
    from ainote.agents.graph import dingtalk_runtime

    dingtalk_runtime._enabled = snap[4]
    dingtalk_runtime._loaded_tool_names = set(snap[5])
    dingtalk_runtime._last_error = snap[6]


def test_status_default_disabled():
    status = get_status()
    assert status["enabled"] is False
    assert status["loaded_tools"] == 0


async def test_enable_loads_tools_and_rebuilds(monkeypatch):
    fake_load = _make_fake_load(["dingtalk_create_event", "dingtalk_send"])
    monkeypatch.setattr("ainote.agents.graph.dingtalk_runtime.load_mcp_tools", fake_load)
    rebuilt = []
    monkeypatch.setattr(
        builder, "rebuild_deferred_and_graph", lambda: rebuilt.append(True)
    )

    result = await enable_dingtalk()

    assert result["enabled"] is True
    assert result["changed"] is True
    assert result["loaded_tools"] == 2
    # Tools merged into ALL_TOOLS
    names = {t.name for t in ALL_TOOLS}
    assert "dingtalk_create_event" in names
    assert "dingtalk_send" in names
    # Registered as MCP tools
    assert "dingtalk_create_event" in MCP_TOOL_NAMES
    # Graph rebuilt
    assert rebuilt == [True]


async def test_enable_idempotent(monkeypatch):
    calls = []

    async def fake_load(include=None, exclude=None):
        calls.append(1)
        tools = [_fake_tool("dingtalk_tool")]
        register_mcp_tools(tools)
        return tools

    monkeypatch.setattr("ainote.agents.graph.dingtalk_runtime.load_mcp_tools", fake_load)
    monkeypatch.setattr(builder, "rebuild_deferred_and_graph", lambda: None)

    await enable_dingtalk()
    result = await enable_dingtalk()  # second call

    assert result["changed"] is False
    assert len(calls) == 1  # load_mcp_tools NOT called again


async def test_disable_removes_tools(monkeypatch):
    fake_load = _make_fake_load(["dingtalk_tool"])
    monkeypatch.setattr("ainote.agents.graph.dingtalk_runtime.load_mcp_tools", fake_load)
    monkeypatch.setattr(builder, "rebuild_deferred_and_graph", lambda: None)

    await enable_dingtalk()
    result = await disable_dingtalk()

    assert result["enabled"] is False
    assert result["changed"] is True
    # Tool removed from ALL_TOOLS and MCP_TOOL_NAMES
    assert "dingtalk_tool" not in {t.name for t in ALL_TOOLS}
    assert "dingtalk_tool" not in MCP_TOOL_NAMES


async def test_disable_idempotent(monkeypatch):
    monkeypatch.setattr("ainote.agents.graph.dingtalk_runtime.load_mcp_tools", lambda **kw: [])
    result = await disable_dingtalk()  # already disabled
    assert result["changed"] is False


async def test_enable_failure_rolls_back(monkeypatch):
    fake_load = _make_fake_load(["dingtalk_tool"])
    monkeypatch.setattr("ainote.agents.graph.dingtalk_runtime.load_mcp_tools", fake_load)

    def boom():
        raise RuntimeError("graph rebuild failed")

    monkeypatch.setattr(builder, "rebuild_deferred_and_graph", boom)

    before_all = list(ALL_TOOLS)
    before_mcp = set(MCP_TOOL_NAMES)

    with pytest.raises(RuntimeError):
        await enable_dingtalk()

    # State unchanged after failure
    assert [t.name for t in ALL_TOOLS] == [t.name for t in before_all]
    assert set(MCP_TOOL_NAMES) == before_mcp
    assert get_status()["enabled"] is False


async def test_enable_no_tools_raises_dingtalk_error(monkeypatch):
    async def fake_load(include=None, exclude=None):
        return []

    monkeypatch.setattr("ainote.agents.graph.dingtalk_runtime.load_mcp_tools", fake_load)

    with pytest.raises(DingTalkError):
        await enable_dingtalk()
    assert get_status()["enabled"] is False
