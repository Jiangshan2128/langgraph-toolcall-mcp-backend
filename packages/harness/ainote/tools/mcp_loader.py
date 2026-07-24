"""Generic MCP server loader — reads ``mcp_servers.json`` and loads tools.

Usage
-----
Add a new MCP server by editing ``mcp_servers.json`` in the project root::

    {
      "mcpServers": {
        "my-server": {
          "enabled": true,
          "type": "stdio",
          "command": "npx",
          "args": ["-y", "@org/my-mcp-server"],
          "env": {
            "API_KEY": "$MY_API_KEY"
          }
        }
      }
    }

Environment variables referenced with ``$VAR`` syntax are resolved from
``os.environ`` at load time.  Unset variables resolve to ``""`` so the
subprocess never sees a literal ``$VAR`` token.

The loader is called once during app startup (``lifespan``).  Tools are
registered via ``register_mcp_tools()`` so the deferred-tool-search system
can manage them.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

# Path resolution priority:
#   1. DEER_FLOW_MCP_SERVERS_PATH env var (legacy compatibility)
#   2. <project_root>/mcp_servers.json
#   3. <project_root>/extensions_config.json (deer-flow compat)
_MCP_CONFIG_NAMES = ("mcp_servers.json", "extensions_config.json")


def _project_root() -> Path:
    """Return the project root (parent of ``app/``)."""
    return Path(__file__).resolve().parents[2]


def _resolve_config_path() -> Path | None:
    """Find the MCP servers config file.

    Returns ``None`` when no config file exists (MCP servers are optional).
    """
    env_path = os.environ.get("MCP_SERVERS_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
        logger.warning("MCP_SERVERS_PATH=%s set but file not found", env_path)

    root = _project_root()
    for name in _MCP_CONFIG_NAMES:
        p = root / name
        if p.exists():
            return p
    return None


def _resolve_env_variables(value: Any) -> Any:
    """Recursively replace ``$VAR`` placeholders with ``os.environ`` values."""
    if isinstance(value, str):
        if value.startswith("$"):
            return os.environ.get(value[1:], "")
        return value
    if isinstance(value, dict):
        return {k: _resolve_env_variables(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_variables(v) for v in value]
    return value


def _build_server_params(
    server_name: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Build a single server params dict for ``MultiServerMCPClient``."""
    transport = config.get("type", "stdio")
    params: dict[str, Any] = {"transport": transport}

    if transport == "stdio":
        command = config.get("command")
        if not command:
            raise ValueError(
                f"MCP server '{server_name}': stdio transport requires 'command'"
            )
        # Windows: npx is a .cmd shim, route through cmd /c
        if sys.platform == "win32" and command == "npx":
            params["command"] = "cmd"
            params["args"] = ["/c", command] + config.get("args", [])
        else:
            params["command"] = command
            params["args"] = config.get("args", [])

        # Merge configured env vars over os.environ so the subprocess can
        # resolve PATH (npx/node etc.) from the parent environment.
        env = {**os.environ}
        if config.get("env"):
            env.update(config["env"])
        params["env"] = env
    elif transport in ("sse", "http"):
        url = config.get("url")
        if not url:
            raise ValueError(
                f"MCP server '{server_name}': {transport} transport requires 'url'"
            )
        params["url"] = url
        if config.get("headers"):
            params["headers"] = config["headers"]
    else:
        raise ValueError(
            f"MCP server '{server_name}': unsupported transport '{transport}'"
        )

    return params


async def load_mcp_tools() -> list[BaseTool]:
    """Load tools from all enabled MCP servers configured in ``mcp_servers.json``.

    Returns an empty list when the config file is missing, empty, or all
    servers fail — the caller should proceed with core tools only.

    Each loaded tool is registered via ``register_mcp_tools()`` so the
    deferred-tool-search system can manage it.
    """
    config_path = _resolve_config_path()
    if config_path is None:
        logger.info("No MCP servers config found (checked %s)", _MCP_CONFIG_NAMES)
        return []

    try:
        with open(config_path, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read MCP servers config %s: %s", config_path, e)
        return []

    servers_config: dict[str, Any] = raw.get("mcpServers") or {}
    if not servers_config:
        logger.info("No 'mcpServers' section in %s", config_path)
        return []

    # Resolve $ENV_VAR placeholders
    servers_config = _resolve_env_variables(servers_config)

    # Filter to enabled servers only
    enabled = {
        name: cfg
        for name, cfg in servers_config.items()
        if cfg.get("enabled", True)
    }
    if not enabled:
        logger.info("All MCP servers are disabled in %s", config_path)
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        logger.error(
            "langchain-mcp-adapters not installed; cannot load MCP tools. "
            "Install: pip install langchain-mcp-adapters"
        )
        return []

    # Build params and connect
    client_params: dict[str, dict[str, Any]] = {}
    for name, cfg in enabled.items():
        try:
            client_params[name] = _build_server_params(name, cfg)
            logger.info("Configured MCP server: %s", name)
        except ValueError as e:
            logger.warning("Skipping MCP server '%s': %s", name, e)

    if not client_params:
        return []

    try:
        client = MultiServerMCPClient(client_params, tool_name_prefix=True)
    except Exception as e:
        logger.error("Failed to create MCP client: %s", e)
        return []

    # Load tools per server (one failure doesn't block others)
    async def _load_one(name: str) -> list[BaseTool]:
        try:
            tools = await client.get_tools(server_name=name)
            logger.info("Loaded %d tool(s) from MCP server '%s'", len(tools), name)
            return tools
        except Exception as e:
            logger.warning(
                "Failed to load tools from MCP server '%s': %s", name, e
            )
            return []

    import asyncio

    all_tools = await asyncio.gather(
        *(_load_one(name) for name in client_params)
    )
    tools = [t for batch in all_tools for t in batch]

    if not tools:
        logger.warning("No MCP tools loaded from any server")
        return []

    # Register as MCP tools (deferred tool search)
    from ainote.tools.tool_search import register_mcp_tools

    register_mcp_tools(tools)

    logger.info(
        "Total MCP tools loaded: %d — %s",
        len(tools),
        [t.name for t in tools],
    )
    return tools
