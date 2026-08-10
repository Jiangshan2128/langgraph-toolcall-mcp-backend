"""Tests for load_mcp_tools include/exclude filtering."""

import json

import pytest

from ainote.tools import mcp_loader


@pytest.fixture
def fake_config(tmp_path, monkeypatch):
    """Write a fake mcp_servers.json with dingtalk + rag servers."""
    config = {
        "mcpServers": {
            "dingtalk": {
                "enabled": True,
                "type": "stdio",
                "command": "npx",
                "args": ["dingtalk-mcp"],
            },
            "rag-knowledge-base": {
                "enabled": True,
                "type": "stdio",
                "command": "python",
                "args": ["server.py"],
            },
        }
    }
    p = tmp_path / "mcp_servers.json"
    p.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(mcp_loader, "_resolve_config_path", lambda: p)
    return p


class _FakeClient:
    """Replaces MultiServerMCPClient: records params, returns tools by server."""

    def __init__(self, client_params, tool_name_prefix=True):
        self.client_params = client_params
        self.calls = []

    async def get_tools(self, server_name=None):
        self.calls.append(server_name)
        return [type("FakeTool", (), {"name": f"{server_name}_tool1"})()]


def _patch_client(monkeypatch, fake_class=_FakeClient):
    """Make load_mcp_tools use the fake client (patched into the module dict)."""
    monkeypatch.setitem(mcp_loader.__dict__, "MultiServerMCPClient", fake_class)


@pytest.mark.asyncio
async def test_load_all_without_filters(fake_config, monkeypatch):
    _patch_client(monkeypatch)

    tools = await mcp_loader.load_mcp_tools()

    names = {t.name for t in tools}
    assert names == {"dingtalk_tool1", "rag-knowledge-base_tool1"}


@pytest.mark.asyncio
async def test_include_only_dingtalk(fake_config, monkeypatch):
    _patch_client(monkeypatch)

    tools = await mcp_loader.load_mcp_tools(include={"dingtalk"})

    names = {t.name for t in tools}
    assert names == {"dingtalk_tool1"}
    assert "rag-knowledge-base_tool1" not in names


@pytest.mark.asyncio
async def test_exclude_dingtalk(fake_config, monkeypatch):
    _patch_client(monkeypatch)

    tools = await mcp_loader.load_mcp_tools(exclude={"dingtalk"})

    names = {t.name for t in tools}
    assert names == {"rag-knowledge-base_tool1"}
    assert "dingtalk_tool1" not in names


@pytest.mark.asyncio
async def test_no_match_returns_empty(fake_config, monkeypatch):
    _patch_client(monkeypatch)

    tools = await mcp_loader.load_mcp_tools(include={"nonexistent"})

    assert tools == []


@pytest.mark.asyncio
async def test_disabled_server_is_skipped_even_if_included(fake_config, monkeypatch):
    """The enabled flag still gates a server even when it's in `include`."""
    config = {
        "mcpServers": {
            "dingtalk": {
                "enabled": False,  # disabled in config
                "type": "stdio",
                "command": "npx",
                "args": ["dingtalk-mcp"],
            }
        }
    }
    fake_config.write_text(json.dumps(config), encoding="utf-8")
    _patch_client(monkeypatch)

    tools = await mcp_loader.load_mcp_tools(include={"dingtalk"})

    assert tools == []


@pytest.mark.asyncio
async def test_env_overrides_applied_last(fake_config, monkeypatch):
    """Per-user env overrides win over the config env block."""
    config = {
        "mcpServers": {
            "dingtalk": {
                "enabled": True,
                "type": "stdio",
                "command": "npx",
                "args": ["dingtalk-mcp"],
                "env": {"DINGTALK_Client_ID": "from-config"},
            }
        }
    }
    fake_config.write_text(json.dumps(config), encoding="utf-8")

    recorded: dict = {}

    class _RecordingClient(_FakeClient):
        def __init__(self, client_params, tool_name_prefix=True):
            super().__init__(client_params, tool_name_prefix)
            recorded["params"] = client_params

    _patch_client(monkeypatch, _RecordingClient)

    await mcp_loader.load_mcp_tools(
        include={"dingtalk"},
        env_overrides={
            "dingtalk": {
                "DINGTALK_Client_ID": "per-user",  # must override from-config
                "ROBOT_ACCESS_TOKEN": "tok",       # new key added
            }
        },
        register=False,
    )

    env = recorded["params"]["dingtalk"]["env"]
    assert env["DINGTALK_Client_ID"] == "per-user"
    assert env["ROBOT_ACCESS_TOKEN"] == "tok"


@pytest.mark.asyncio
async def test_register_false_skips_global_registration(fake_config, monkeypatch):
    """register=False must not touch the global MCP_TOOL_NAMES."""
    from ainote.tools.tool_search import MCP_TOOL_NAMES

    MCP_TOOL_NAMES.clear()
    _patch_client(monkeypatch)

    tools = await mcp_loader.load_mcp_tools(include={"dingtalk"}, register=False)

    assert tools
    assert not any(name in MCP_TOOL_NAMES for name in {t.name for t in tools})
