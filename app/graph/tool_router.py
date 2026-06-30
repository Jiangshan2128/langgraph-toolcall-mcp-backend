"""Tool binding with deferred DingTalk MCP tool search.

Core tools are always bound. If DingTalk MCP tools are loaded, a
``tool_search`` tool is dynamically created and added to ``ALL_TOOLS``,
allowing the LLM to discover and promote MCP tools at runtime via
graph state (``promoted_tools``).
"""

import logging

from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.agents.config import get_model
from app.tools import ALL_TOOLS

logger = logging.getLogger(__name__)

# Tools that are ALWAYS bound to the LLM.
_CORE_TOOL_NAMES: frozenset[str] = frozenset({
    "update_profile",
    "update_tasks",
    "update_instructions",
    "get_tasks",
    "mark_task_done",
    "update_task_priority",
    "delete_task_by_title",
    "web_search",
})


def get_model_with_tools(
    *,
    promoted_names: list[str] | None = None,
) -> ChatOpenAI:
    """Return a model with the appropriate tools bound.

    Binding strategy:
      1. Core tools are always bound.
      2. ``tool_search`` (if in ALL_TOOLS) is always bound.
      3. Promoted tool names (from ``state["promoted_tools"]``) have their
         full schemas bound so the LLM can call them.
    """
    model = get_model()
    tool_map = {t.name: t for t in ALL_TOOLS}

    # 1. Core tools are always bound.
    tools_to_bind: list[BaseTool] = [
        tool_map[name] for name in _CORE_TOOL_NAMES if name in tool_map
    ]

    # 2. tool_search is always bound if present.
    if "tool_search" in tool_map:
        tools_to_bind.append(tool_map["tool_search"])

    # 3. Add promoted (previously searched) MCP tools.
    if promoted_names:
        for name in promoted_names:
            if name in tool_map and name not in _CORE_TOOL_NAMES and name != "tool_search":
                tools_to_bind.append(tool_map[name])

    logger.info(
        "Binding %d tools: core=%d, tool_search=%s, promoted=%d",
        len(tools_to_bind),
        len([n for n in _CORE_TOOL_NAMES if n in tool_map]),
        "tool_search" in tool_map,
        len(promoted_names or []),
    )
    return model.bind_tools(tools_to_bind)
