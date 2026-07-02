"""External integrations — web search and DingTalk MCP."""

from app.tools.community.dingtalk import load_dingtalk_tools
from app.tools.community.search import web_search
from app.tools.mcp_loader import load_mcp_tools

__all__ = [
    "load_dingtalk_tools",
    "load_mcp_tools",
    "web_search",
]
