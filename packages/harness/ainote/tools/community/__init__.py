"""External integrations — web search and DingTalk MCP."""

from ainote.tools.community.dingtalk import load_dingtalk_tools
from ainote.tools.community.search import web_search
from ainote.tools.mcp_loader import load_mcp_tools

__all__ = [
    "load_dingtalk_tools",
    "load_mcp_tools",
    "web_search",
]
