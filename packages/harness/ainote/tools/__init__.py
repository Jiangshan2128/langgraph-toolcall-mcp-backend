from ainote.tools.core.memory import update_instructions, update_profile, update_tasks
from ainote.tools.community.search import web_search
from ainote.tools.core.tasks import (
    delete_task_by_title,
    get_tasks_tool,
    mark_task_done,
    update_task_priority,
)

__all__ = [
    "update_profile",
    "update_tasks",
    "update_instructions",
    "web_search",
    "get_tasks_tool",
    "mark_task_done",
    "update_task_priority",
    "delete_task_by_title",
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
]
