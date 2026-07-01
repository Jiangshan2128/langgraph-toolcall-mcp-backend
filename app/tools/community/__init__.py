"""External integrations — web search and DingTalk MCP."""

from app.tools.community.dingtalk import load_dingtalk_tools
from app.tools.community.search import web_search

__all__ = [
    "load_dingtalk_tools",
    "web_search",
]
