"""Runtime state + toggle operations for the DingTalk MCP connection.

DingTalk MCP is EXCLUDED from app startup (its ``npx`` subprocess is the
biggest cold-start cost). It is loaded on demand via
``POST /api/v1/dingtalk/enable`` and unloaded via
``POST /api/v1/dingtalk/disable``.

Key fact about ``MultiServerMCPClient`` (verified against source): it never
holds a long-lived session — ``get_tools()`` spins up a stdio subprocess,
lists tools, and closes it per call. The returned ``BaseTool`` embeds the
``connection`` *config*, not a live session; each real tool invocation starts
and stops its own subprocess. So enabling/disabling is purely a matter of
adding/removing tool references and rebuilding the graph — there is no
connection to close.

Single-worker deployment (``--workers 1``), so a module-level ``asyncio.Lock``
plus a couple of module globals is enough.
"""

from __future__ import annotations

import asyncio
import logging

from ainote.agents.graph import builder
from ainote.agents.graph.deferred_cache import (
    get_deferred_setup_cached,
    refresh_deferred_setup,
)
from ainote.tools import ALL_TOOLS, remove_tools_by_name
from ainote.tools.mcp_loader import load_mcp_tools
from ainote.tools.tool_search import MCP_TOOL_NAMES, unregister_mcp_tools

logger = logging.getLogger(__name__)

DINGTALK_SERVER = "dingtalk"

# Serialize enable/disable/rebuild critical sections.
_lock: asyncio.Lock = asyncio.Lock()
_enabled: bool = False
_loaded_tool_names: set[str] = set()
_last_error: str | None = None


class DingTalkError(RuntimeError):
    """Raised when enabling DingTalk fails (kept as a distinct type)."""


def get_status() -> dict:
    """Return a JSON-safe snapshot of the DingTalk runtime state."""
    return {
        "enabled": _enabled,
        "server": DINGTALK_SERVER,
        "loaded_tools": len(_loaded_tool_names),
        "tool_names": sorted(_loaded_tool_names),
        "last_error": _last_error,
    }


def _snapshot():
    """Snapshot the shared global state so a failed toggle can roll back."""
    return (
        list(ALL_TOOLS),
        set(MCP_TOOL_NAMES),
        get_deferred_setup_cached(),
        builder.graph,
    )


def _restore(snap) -> None:
    all_list, mcp_names, setup, graph = snap
    ALL_TOOLS[:] = all_list
    MCP_TOOL_NAMES.clear()
    MCP_TOOL_NAMES.update(mcp_names)
    refresh_deferred_setup(setup)
    builder.graph = graph


async def enable_dingtalk() -> dict:
    """Load DingTalk MCP tools and rebuild the graph. Idempotent.

    Returns ``{"enabled": True, "changed": bool, "loaded_tools": int}``.
    Raises ``DingTalkError`` on failure (state is rolled back).
    """
    global _enabled, _loaded_tool_names, _last_error
    async with _lock:
        if _enabled:
            # Idempotent: already on → no reload.
            return {
                "enabled": True,
                "changed": False,
                "loaded_tools": len(_loaded_tool_names),
            }

        # Snapshot BEFORE loading so a failed enable fully rolls back — this
        # includes undoing register_mcp_tools() (MCP_TOOL_NAMES) performed
        # inside load_mcp_tools().
        snap = _snapshot()

        tools = await load_mcp_tools(include={DINGTALK_SERVER})
        if not tools:
            _last_error = (
                "No DingTalk tools loaded — check DINGTALK_CLIENT_ID/SECRET "
                "and that 'dingtalk' is enabled in mcp_servers.json"
            )
            _restore(snap)
            raise DingTalkError(_last_error)

        names = {t.name for t in tools}
        try:
            existing = {t.name for t in ALL_TOOLS}
            for t in tools:
                if t.name not in existing:
                    ALL_TOOLS.append(t)
                    existing.add(t.name)
            builder.rebuild_deferred_and_graph()
        except Exception:
            _restore(snap)
            raise

        _enabled = True
        _loaded_tool_names = names
        _last_error = None
        logger.info("DingTalk MCP enabled: %d tool(s)", len(names))
        return {"enabled": True, "changed": True, "loaded_tools": len(names)}


async def disable_dingtalk() -> dict:
    """Unload DingTalk MCP tools and rebuild the graph. Idempotent.

    Returns ``{"enabled": False, "changed": bool, "loaded_tools": 0}``.
    """
    global _enabled, _loaded_tool_names, _last_error
    async with _lock:
        if not _enabled:
            return {"enabled": False, "changed": False, "loaded_tools": 0}

        names = _loaded_tool_names
        snap = _snapshot()
        try:
            # Order matters: unregister MCP names BEFORE rebuilding, because
            # build_deferred_tool_setup filters deferred tools by is_mcp_tool
            # (= name in MCP_TOOL_NAMES).
            remove_tools_by_name(names)
            unregister_mcp_tools(names)
            builder.rebuild_deferred_and_graph()
        except Exception:
            _restore(snap)
            raise

        _enabled = False
        _loaded_tool_names = set()
        _last_error = None
        logger.info("DingTalk MCP disabled (%d tool(s) removed)", len(names))
        return {"enabled": False, "changed": True, "loaded_tools": 0}
