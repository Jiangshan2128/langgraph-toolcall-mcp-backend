from app.tools.memory import update_instructions, update_profile, update_tasks
from app.tools.search import web_search
from app.tools.tasks import (
    delete_task_by_title,
    mark_task_done,
    update_task_priority,
)

__all__ = [
    "update_profile",
    "update_tasks",
    "update_instructions",
    "web_search",
    "mark_task_done",
    "update_task_priority",
    "delete_task_by_title",
]

ALL_TOOLS = [
    update_profile,
    update_tasks,
    update_instructions,
    web_search,
    mark_task_done,
    update_task_priority,
    delete_task_by_title,
]
