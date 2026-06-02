"""任务域依赖注入"""
from uuid import UUID

from appBackup.task.service import todo_service
from appBackup.task.exceptions import TodoListNotFound, TaskNotFound


def get_todo_service():
    return todo_service


async def valid_todo_list_id(list_id: UUID) -> dict:
    """校验清单是否存在，用于依赖链"""
    todo_list = todo_service.get_list(list_id)
    if not todo_list:
        raise TodoListNotFound(str(list_id))
    return todo_list


async def valid_task_id(task_id: UUID) -> dict:
    """校验任务是否存在，用于依赖链"""
    task = todo_service.get_task(task_id)
    if not task:
        raise TaskNotFound(str(task_id))
    return task
