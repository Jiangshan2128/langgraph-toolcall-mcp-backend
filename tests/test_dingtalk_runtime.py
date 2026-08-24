"""Tests for the per-user DingTalk MCP runtime (dingtalk_runtime.py).

These patch ``load_mcp_tools`` so no real network or npx subprocess is ever
started. The key regression this suite guards: enabling DingTalk for one user
must NEVER mutate the shared ``ALL_TOOLS`` / ``MCP_TOOL_NAMES`` / graph — the
whole point of the per-user redesign.
"""

import pytest

from ainote.agents.graph.dingtalk_runtime import (
    DingTalkConfigError,
    DingTalkError,
    DingTalkRuntime,
    configure_runtime,
    disable_dingtalk,
    enable_dingtalk,
    ensure_user_tools,
    get_status,
    get_user_deferred_setup,
    get_user_runtime,
    get_user_tool_node,
    mark_user_connected,
)
from ainote.agents.graph.memory import get_dingtalk_config, put_dingtalk_config
from ainote.tools import ALL_TOOLS
from ainote.tools.core.tool_search import MCP_TOOL_NAMES

CREDS = {"client_id": "cid", "client_secret": "sec"}


def _fake_tool(name):
    """Build a REAL BaseTool so ToolNode / deferred catalog accept it."""
    from langchain_core.tools import StructuredTool

    async def _run() -> str:
        return "ok"

    return StructuredTool.from_function(
        func=_run,
        name=name,
        description="fake",
    )


def _make_fake_load(tool_names, calls=None):
    """Build a load_mcp_tools fake honoring the new kwargs signature."""
    async def fake_load(include=None, exclude=None, *, env_overrides=None, register=True):
        if calls is not None:
            calls.append((env_overrides, register))
        return [_fake_tool(n) for n in tool_names]

    return fake_load


@pytest.fixture(autouse=True)
def _reset_runtime():
    """Unconfigure the module-level runtime pointer between tests."""
    configure_runtime(None)
    yield
    configure_runtime(None)


@pytest.fixture
def store():
    """Point the module accessor at an isolated DingTalkRuntime + InMemoryStore.

    Mirrors what the app container does at startup (``configure_runtime``), but
    with a fresh instance + store per test so no state leaks across tests.
    """
    from langgraph.store.memory import InMemoryStore

    s = InMemoryStore()
    configure_runtime(DingTalkRuntime(store=s))
    return s


def _stored(store, user_id):
    return get_dingtalk_config(store, user_id)


# ── status ──────────────────────────────────────────────────────────────


def test_status_default_disabled(store):
    status = get_status("user-1")
    assert status["user_id"] == "user-1"
    assert status["enabled"] is False
    assert status["loaded_tools"] == 0
    assert status["credentials_configured"] is False
    assert "client_secret" not in status  # never echo secrets


# ── enable ──────────────────────────────────────────────────────────────


async def test_enable_loads_tools_for_user(monkeypatch, store):
    fake_load = _make_fake_load(["dingtalk_create_event", "dingtalk_send"])
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools", fake_load
    )

    result = await enable_dingtalk("user-1", dict(CREDS))

    assert result["enabled"] is True
    assert result["changed"] is True
    assert result["loaded_tools"] == 2
    # Shared globals untouched
    assert "dingtalk_create_event" not in {t.name for t in ALL_TOOLS}
    assert "dingtalk_create_event" not in MCP_TOOL_NAMES
    # Per-user runtime populated
    assert get_user_runtime("user-1") is not None
    assert get_user_tool_node("user-1") is not None
    assert get_user_deferred_setup("user-1") is not None
    # Persisted
    cfg = _stored(store, "user-1")
    assert cfg["enabled"] is True
    assert cfg["client_id"] == "cid"
    assert cfg["client_secret"] == "sec"


async def test_enable_passes_env_overrides_and_register_false(monkeypatch, store):
    calls = []
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools",
        _make_fake_load(["dingtalk_tool"], calls=calls),
    )

    await enable_dingtalk(
        "user-1",
        {
            "client_id": "cid",
            "client_secret": "sec",
            "agent_id": "123",
            "robot_token": "tok",
            "active_profiles": ["todo", "contact"],
        },
    )

    env_overrides, register = calls[0]
    ov = env_overrides["dingtalk"]
    assert ov["DINGTALK_Client_ID"] == "cid"
    assert ov["DINGTALK_Client_Secret"] == "sec"
    assert ov["DINGTALK_AGENT_ID"] == "123"
    assert ov["ROBOT_ACCESS_TOKEN"] == "tok"
    assert ov["ACTIVE_PROFILES"] == "todo,contact"
    assert register is False  # must not pollute global MCP_TOOL_NAMES


async def test_enable_idempotent_same_creds(monkeypatch, store):
    calls = []
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools",
        _make_fake_load(["dingtalk_tool"], calls=calls),
    )

    await enable_dingtalk("user-1", dict(CREDS))
    result = await enable_dingtalk("user-1", dict(CREDS))

    assert result["changed"] is False
    assert len(calls) == 1  # load_mcp_tools NOT called again


async def test_enable_merges_over_stored_creds(monkeypatch, store):
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools",
        _make_fake_load(["dingtalk_tool"]),
    )
    # First enable with full creds, then re-enable passing only a new token.
    await enable_dingtalk(
        "user-1", {**CREDS, "robot_token": "tok1", "agent_id": "123"}
    )
    result = await enable_dingtalk("user-1", {"robot_token": "tok2"})

    assert result["changed"] is True  # creds differ → reload
    cfg = _stored(store, "user-1")
    assert cfg["robot_token"] == "tok2"
    assert cfg["client_id"] == "cid"  # previously stored creds preserved


async def test_enable_missing_creds_raises_config_error(store):
    with pytest.raises(DingTalkConfigError):
        await enable_dingtalk("user-1")  # nothing stored → no client_id
    assert get_status("user-1")["enabled"] is False


async def test_enable_no_tools_raises_and_rolls_back(monkeypatch, store):
    async def fake_load(include=None, exclude=None, *, env_overrides=None, register=True):
        return []

    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools", fake_load
    )

    with pytest.raises(DingTalkError):
        await enable_dingtalk("user-1", dict(CREDS))

    # Rolled back: not enabled, error surfaced, creds saved but not enabled
    status = get_status("user-1")
    assert status["enabled"] is False
    assert status["last_error"] is not None
    cfg = _stored(store, "user-1")
    assert cfg["enabled"] is False
    assert cfg["client_id"] == "cid"
    # No stray runtime
    assert get_user_runtime("user-1") is None


# ── disable ─────────────────────────────────────────────────────────────


async def test_disable_removes_runtime_keeps_creds(monkeypatch, store):
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools",
        _make_fake_load(["dingtalk_tool"]),
    )
    await enable_dingtalk("user-1", dict(CREDS))

    result = await disable_dingtalk("user-1")

    assert result["enabled"] is False
    assert result["changed"] is True
    assert get_user_runtime("user-1") is None
    assert get_user_tool_node("user-1") is None
    assert get_user_deferred_setup("user-1") is None
    # Credentials kept so re-enable works without re-entering them
    cfg = _stored(store, "user-1")
    assert cfg["enabled"] is False
    assert cfg["client_id"] == "cid"


async def test_disable_idempotent(store):
    result = await disable_dingtalk("user-1")
    assert result["changed"] is False


async def test_re_enable_with_stored_creds_after_disable(monkeypatch, store):
    calls = []
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools",
        _make_fake_load(["dingtalk_tool"], calls=calls),
    )
    await enable_dingtalk("user-1", dict(CREDS))
    await disable_dingtalk("user-1")

    result = await enable_dingtalk("user-1")  # no creds → reuse stored

    assert result["enabled"] is True
    assert result["changed"] is True
    assert len(calls) == 2


# ── user isolation ──────────────────────────────────────────────────────


async def test_users_are_isolated(monkeypatch, store):
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools",
        _make_fake_load(["dingtalk_tool_a"]),
    )
    await enable_dingtalk("user-1", dict(CREDS))

    # user-2 sees nothing
    assert get_user_runtime("user-2") is None
    assert get_user_tool_node("user-2") is None
    assert get_user_deferred_setup("user-2") is None
    assert get_status("user-2")["enabled"] is False
    # user-1 has everything
    assert get_user_runtime("user-1") is not None
    assert get_user_tool_node("user-1") is not None


# ── ensure_user_tools (lazy restore after restart) ─────────────────────


async def test_ensure_user_tools_restores_from_store(monkeypatch, store):
    # Simulate a restart: only the store has the enabled config.
    put_dingtalk_config(store, "user-1", {**CREDS, "enabled": True})
    calls = []
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools",
        _make_fake_load(["dingtalk_tool"], calls=calls),
    )

    rt = await ensure_user_tools("user-1")

    assert rt.enabled is True
    assert get_user_tool_node("user-1") is not None
    assert len(calls) == 1
    # Second ensure is a registry hit — no re-load
    await ensure_user_tools("user-1")
    assert len(calls) == 1


async def test_ensure_user_tools_disabled_user_noop(store):
    rt = await ensure_user_tools("user-1")  # nothing in store
    assert rt.enabled is False
    assert get_user_tool_node("user-1") is None


async def test_ensure_user_tools_failed_load_not_retried(monkeypatch, store):
    put_dingtalk_config(store, "user-1", {**CREDS, "enabled": True})
    calls = []

    async def failing_load(include=None, exclude=None, *, env_overrides=None, register=True):
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools", failing_load
    )

    rt = await ensure_user_tools("user-1")
    assert rt.enabled is False
    assert rt.load_failed is True
    assert rt.last_error == "boom"
    # Never raises into the graph; NOT re-spawned on every turn
    await ensure_user_tools("user-1")
    assert len(calls) == 1


async def test_ensure_user_tools_loads_when_enabled_but_no_tools(monkeypatch, store):
    """Regression: OAuth callback marks connected (enabled, empty tools) — the
    first chat turn must still LOAD the tools, not short-circuit on an
    enabled-but-tool-less runtime.

    Production symptom: after a fresh deploy, "查看钉钉待办" answered "cannot
    query" because `ensure_user_tools` returned the enabled runtime with
    `tools=[]` (mark_user_connected sets enabled only), so `get_user_runtime`
    → None and tool_binder bound no DingTalk tools (tool_search=False).
    """
    put_dingtalk_config(store, "user-1", {**CREDS, "enabled": True})
    calls = []

    async def fake_load(include=None, exclude=None, *, env_overrides=None, register=True):
        calls.append(1)
        return [_fake_tool("dingtalk_queryTasks")]

    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools", fake_load
    )

    # Simulate the OAuth callback: mark_user_connected creates an enabled
    # runtime WITHOUT loading tools (that's the production path).
    await mark_user_connected("user-1")
    assert get_user_runtime("user-1") is None  # enabled but tools=[] → not usable

    rt = await ensure_user_tools("user-1")

    assert rt.enabled is True
    assert get_user_runtime("user-1") is not None  # now usable
    assert get_user_tool_node("user-1") is not None
    assert "dingtalk_queryTasks" in get_user_tool_node("user-1").tools_by_name
    assert len(calls) == 1  # loaded exactly once
    # Second turn is a registry hit — no re-load.
    await ensure_user_tools("user-1")
    assert len(calls) == 1


# ── shared globals invariant ────────────────────────────────────────────


async def test_shared_globals_never_mutated_across_ops(monkeypatch, store):
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.load_mcp_tools",
        _make_fake_load(["dingtalk_tool"]),
    )
    before_tools = [t.name for t in ALL_TOOLS]
    before_mcp = set(MCP_TOOL_NAMES)

    await enable_dingtalk("user-1", dict(CREDS))
    await disable_dingtalk("user-1")
    await enable_dingtalk("user-2", dict(CREDS))

    assert [t.name for t in ALL_TOOLS] == before_tools
    assert set(MCP_TOOL_NAMES) == before_mcp
