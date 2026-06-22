"""DingTalk server-side MCP integration.

The DingTalk MCP server (`dingtalk-mcp` on npm) is a **stdio** server: it runs
as a child process started via `npx -y dingtalk-mcp@latest`, authenticated through
environment variables (AppKey/AppSecret). We connect to it with
`langchain-mcp-adapters`' `MultiServerMCPClient`.

Notes
-----
* `MultiServerMCPClient.get_tools()` returns LangChain tools that open a **fresh
  stdio session per call** (the client holds no persistent connection). That makes
  the tools event-loop-agnostic: they can be loaded during app startup and invoked
  later from any request's loop. No `close()` is required.
* Each tool call spawns the `dingtalk-mcp` subprocess, so there is per-call cold
  start cost. The access_token is fetched from DingTalk on every call (the server
  caches it only for the lifetime of that process).
* Loading is opt-in: when `DINGTALK_MCP_ENABLED` is false or credentials are
  missing, `load_dingtalk_tools()` returns an empty list and the agent keeps
  working with its core tools.
"""

import logging
import os
import sys
from typing import Any

from langchain_core.tools import BaseTool

from app.core.config import settings

logger = logging.getLogger(__name__)

# Reference to the client (kept for clarity / future use; tools are self-contained
# and do not depend on this reference staying alive).
_client: Any = None


def _dingtalk_env() -> dict[str, str]:
    """Build the env dict passed to the dingtalk-mcp subprocess.

    Merged over `os.environ` so that npx/node can resolve PATH.
    """
    env = {**os.environ}
    env["DINGTALK_Client_ID"] = settings.DINGTALK_CLIENT_ID
    env["DINGTALK_Client_Secret"] = settings.DINGTALK_CLIENT_SECRET
    env["ACTIVE_PROFILES"] = settings.DINGTALK_ACTIVE_PROFILES or "ALL"
    if settings.DINGTALK_AGENT_ID:
        env["DINGTALK_AGENT_ID"] = settings.DINGTALK_AGENT_ID
    if settings.ROBOT_CODE:
        env["ROBOT_CODE"] = settings.ROBOT_CODE
    if settings.ROBOT_ACCESS_TOKEN:
        env["ROBOT_ACCESS_TOKEN"] = settings.ROBOT_ACCESS_TOKEN
    return env


def _npx_command() -> tuple[str, list[str]]:
    """Return (command, args) to launch `dingtalk-mcp` via npx.

    On Windows `npx` is a `.cmd` shim that `asyncio.create_subprocess_exec` cannot
    launch directly, so we route through `cmd /c`.
    """
    pkg = "dingtalk-mcp@latest"
    if sys.platform == "win32":
        return "cmd", ["/c", "npx", "-y", pkg]
    return "npx", ["-y", pkg]


async def load_dingtalk_tools() -> list[BaseTool]:
    """Connect to the DingTalk MCP server and return its LangChain tools.

    Returns an empty list when disabled, misconfigured, or on failure — the
    caller (graph build) proceeds with core tools only.
    """
    global _client

    if not settings.DINGTALK_MCP_ENABLED:
        logger.info("DingTalk MCP disabled (DINGTALK_MCP_ENABLED=false).")
        return []
    if not settings.DINGTALK_CLIENT_ID or not settings.DINGTALK_CLIENT_SECRET:
        logger.warning(
            "DingTalk MCP enabled but DINGTALK_CLIENT_ID/SECRET not set; skipping."
        )
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        logger.error(
            "langchain-mcp-adapters not installed; cannot load DingTalk MCP tools."
        )
        return []

    command, args = _npx_command()
    try:
        client = MultiServerMCPClient(
            {
                "dingtalk": {
                    "transport": "stdio",
                    "command": command,
                    "args": args,
                    "env": _dingtalk_env(),
                }
            }
        )
        tools = await client.get_tools()
        _client = client
        logger.info(
            "Loaded %d DingTalk MCP tools: %s",
            len(tools),
            [t.name for t in tools],
        )
        return tools
    except Exception:
        logger.exception(
            "Failed to load DingTalk MCP tools; continuing without them."
        )
        return []
