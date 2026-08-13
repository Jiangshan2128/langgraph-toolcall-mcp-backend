"""External integrations — web search and DingTalk MCP."""

from ainote.tools.community.search import web_search
from ainote.tools.mcp_loader import load_mcp_tools

__all__ = [
    "load_mcp_tools",
    "web_search",
]
