from ainote.tools.core.memory import update_instructions, update_profile, update_tasks
from ainote.tools.community.search import web_search
from ainote.tools.core.tasks import (
    delete_task_by_title,
    get_tasks_tool,
    mark_task_done,
    update_task_priority,
)
from ainote.tools.core.time import get_current_time

__all__ = [
    "update_profile",
    "update_tasks",
    "update_instructions",
    "web_search",
    "get_tasks_tool",
    "mark_task_done",
    "update_task_priority",
    "delete_task_by_title",
    "get_current_time",
]

ALL_TOOLS = [
    update_profile,
    update_tasks,
    update_instructions,
    web_search,
    get_tasks_tool,
    mark_task_done,
    update_task_priority,
    delete_task_by_title,
    get_current_time,
]


def remove_tools_by_name(names: set[str]) -> list:
    """Remove tools whose name is in ``names`` from ALL_TOOLS (in place).

    ALL_TOOLS is a module-level list shared by ToolNode and tool_binder, so we
    replace it in place (``ALL_TOOLS[:] = ...``) to keep the object identity —
    anyone holding a reference to the original list sees the removal.

    Returns the removed tools.
    """
    removed, keep = [], []
    for t in ALL_TOOLS:
        (removed if t.name in names else keep).append(t)
    ALL_TOOLS[:] = keep
    return removed
