"""Two-stage tool router for models with tool-count limits.

Problem
-------
GLM-5.x rejects requests with > ~55 tools (API error 1210).
When DingTalk MCP is enabled the total can reach 106 tools.

Solution
--------
Before the main agent call, a lightweight "tool router" LLM call selects
the relevant tools based on the user's message.  Only the selected tools'
full schemas are then bound to the main agent model.

The router receives only tool *names* + one-line descriptions (not full
parameter schemas), so it stays well within token limits even with 100+
tools.

If the total tool count is within the model's limit, the router is skipped
entirely (zero overhead for the common case).

Usage
-----
Any node that needs a model with tools bound should call:

    model = await get_model_with_tools(messages)

instead of:

    model = get_model()
    tools = await select_tools(model, messages, ALL_TOOLS)
    model = model.bind_tools(tools)
"""

import json
import logging

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.agents.config import get_model
from app.core.config import settings
from app.tools import ALL_TOOLS

logger = logging.getLogger(__name__)

TOOL_ROUTER_SYSTEM = """\
You are a tool selector. Given the user's message, pick the tools that are
relevant or potentially useful for handling it.

Rules:
- Return a JSON object with a single key "selected_tools" whose value is a
  list of tool name strings.
- Always include core tools (update_profile, update_tasks, update_instructions,
  get_tasks, mark_task_done, update_task_priority, delete_task_by_title,
  web_search) if they appear in the catalog — they are always potentially
  relevant.
- Select at most {max_tools} tools.
- If unsure, prefer including a tool over excluding it.
- Return ONLY the JSON object, no other text.

Available tools:
{tool_catalog}\
"""


def _needs_routing(tools: list[BaseTool]) -> bool:
    cap = settings.GLM_MAX_TOOLS
    return cap > 0 and len(tools) > cap


def _build_catalog(tools: list[BaseTool]) -> str:
    lines = []
    for t in tools:
        desc = (t.description or "").split("\n")[0].strip()
        lines.append(f"- {t.name}: {desc}")
    return "\n".join(lines)


def _core_tool_names() -> set[str]:
    return {
        "update_profile",
        "update_tasks",
        "update_instructions",
        "get_tasks",
        "mark_task_done",
        "update_task_priority",
        "delete_task_by_title",
        "web_search",
    }


async def select_tools(
    model: ChatOpenAI,
    messages: list[BaseMessage],
    all_tools: list[BaseTool],
) -> list[BaseTool]:
    """Select relevant tools via a lightweight LLM call.

    If the total tool count is within the model limit, returns ``all_tools``
    unchanged (zero overhead).
    """
    cap = settings.GLM_MAX_TOOLS
    if not _needs_routing(all_tools):
        return all_tools

    catalog = _build_catalog(all_tools)
    system = TOOL_ROUTER_SYSTEM.format(
        max_tools=cap,
        tool_catalog=catalog,
    )

    router_messages = [
        SystemMessage(content=system),
        *messages,
    ]

    try:
        response = await model.ainvoke(router_messages)
        content = response.content.strip()

        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        parsed = json.loads(content)
        selected_names: list[str] = parsed.get("selected_tools", [])
    except (json.JSONDecodeError, KeyError, AttributeError) as exc:
        logger.warning("Tool router parse error, falling back to core tools: %s", exc)
        selected_names = list(_core_tool_names())
    except Exception as exc:
        logger.warning("Tool router call failed, falling back to core tools: %s", exc)
        selected_names = list(_core_tool_names())

    core_names = _core_tool_names()
    for name in core_names:
        if name not in selected_names:
            selected_names.append(name)

    selected_names = selected_names[:cap]

    tool_map = {t.name: t for t in all_tools}
    selected = [tool_map[n] for n in selected_names if n in tool_map]

    if not selected:
        selected = [t for t in all_tools if t.name in core_names]

    logger.info(
        "Tool router selected %d/%d tools: %s",
        len(selected),
        len(all_tools),
        [t.name for t in selected],
    )
    return selected


async def get_model_with_tools(
    messages: list[BaseMessage],
    *,
    all_tools: list[BaseTool] | None = None,
) -> ChatOpenAI:
    """Return a model with the appropriate tools already bound.

    This is the single entry point any node should use when it needs a
    model that can call tools.  It handles:

    1. Getting the base model via ``get_model()``.
    2. Selecting a relevant tool subset if the total exceeds
       ``settings.GLM_MAX_TOOLS`` (two-stage routing).
    3. Binding the selected tools to the model.

    Parameters
    ----------
    messages : list[BaseMessage]
        The conversation messages (used by the router to decide which
        tools are relevant).
    all_tools : list[BaseTool] | None
        The full tool catalog.  Defaults to ``app.tools.ALL_TOOLS``.

    Returns
    -------
    ChatOpenAI
        A model instance with ``.bind_tools()`` already applied.
    """
    if all_tools is None:
        all_tools = ALL_TOOLS

    model = get_model()
    tools = await select_tools(model, messages, all_tools)
    return model.bind_tools(tools)