"""DingTalk MCP integration — thin wrapper around the generic MCP loader.

.. deprecated::
    This module exists for backward compatibility.  New code should use
    ``app.tools.mcp_loader.load_mcp_tools()`` directly.  The DingTalk
    server is now configured in ``mcp_servers.json`` instead of hard-coded
    environment variables.
"""

import logging

from app.tools.mcp_loader import load_mcp_tools

logger = logging.getLogger(__name__)


async def load_dingtalk_tools():
    """Load DingTalk MCP tools via the generic ``mcp_servers.json`` loader.

    .. deprecated::
        Delegates to ``load_mcp_tools()``.  Kept for backward-compatible
        imports from ``builder.py`` and ``community/__init__.py``.
    """
    logger.warning(
        "load_dingtalk_tools() is deprecated — use load_mcp_tools() from "
        "app.tools.mcp_loader instead."
    )
    return await load_mcp_tools()
