"""任务域业务逻辑 — 内存存储（后续替换为数据库）"""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from appBackup.task.schemas import (
    TaskCreate,
    TaskStatus,
    TaskUpdate,
    TodoListCreate,
    TodoListUpdate,
)
from appBackup.task.exceptions import TodoListNotFound, TaskNotFound


# ---------- 内存存储 ----------
_todo_lists: dict[UUID, dict] = {}
_tasks: dict[UUID, dict] = {}


class TodoService:
    """任务与清单的业务逻辑"""

    # ==================== 清单操作 ====================

    def create_list(self, data: TodoListCreate) -> dict:
        """清单创建"""
        now = datetime.now(timezone.utc)
        todo_list = {
            "id": uuid4(),
            "title": data.title,
            "description": data.description,
            "created_at": now,
            "updated_at": now,
        }
        _todo_lists[todo_list["id"]] = todo_list
        return todo_list

    def get_list(self, list_id: UUID) -> dict | None:
        """获取单个清单"""
        return _todo_lists.get(list_id)

    def get_lists(self) -> list[dict]:
        """获取所有清单"""
        return list(_todo_lists.values())

    def update_list(self, list_id: UUID, data: TodoListUpdate) -> dict:
        """修改清单"""
        todo_list = _todo_lists.get(list_id)
        if not todo_list:
            raise TodoListNotFound(str(list_id))

        if data.title is not None:
            todo_list["title"] = data.title
        if data.description is not None:
            todo_list["description"] = data.description
        todo_list["updated_at"] = datetime.now(timezone.utc)
        return todo_list

    def delete_list(self, list_id: UUID) -> None:
        """删除清单及其所有任务"""
        if list_id not in _todo_lists:
            raise TodoListNotFound(str(list_id))
        del _todo_lists[list_id]
        # 级联删除该清单下所有任务
        to_delete = [tid for tid, t in _tasks.items() if t["todo_list_id"] == list_id]
        for tid in to_delete:
            del _tasks[tid]

    # ==================== 任务操作 ====================

    def create_task(self, list_id: UUID, data: TaskCreate) -> dict:
        """在清单下创建任务"""
        if list_id not in _todo_lists:
            raise TodoListNotFound(str(list_id))

        now = datetime.now(timezone.utc)
        task = {
            "id": uuid4(),
            "todo_list_id": list_id,
            "title": data.title,
            "description": data.description,
            "assignee": data.assignee,
            "status": TaskStatus.PENDING,
            "created_at": now,
            "updated_at": now,
        }
        _tasks[task["id"]] = task
        return task

    def get_task(self, task_id: UUID) -> dict | None:
        """获取单个任务"""
        return _tasks.get(task_id)

    def get_tasks_by_list(self, list_id: UUID) -> list[dict]:
        """获取清单下所有任务"""
        if list_id not in _todo_lists:
            raise TodoListNotFound(str(list_id))
        return [t for t in _tasks.values() if t["todo_list_id"] == list_id]

    def assign_task(self, task_id: UUID, assignee: str) -> dict:
        """任务指派"""
        task = _tasks.get(task_id)
        if not task:
            raise TaskNotFound(str(task_id))
        task["assignee"] = assignee
        task["updated_at"] = datetime.now(timezone.utc)
        return task

    def update_task(self, task_id: UUID, data: TaskUpdate) -> dict:
        """任务修改"""
        task = _tasks.get(task_id)
        if not task:
            raise TaskNotFound(str(task_id))

        if data.title is not None:
            task["title"] = data.title
        if data.description is not None:
            task["description"] = data.description
        if data.assignee is not None:
            task["assignee"] = data.assignee
        if data.status is not None:
            task["status"] = data.status
        task["updated_at"] = datetime.now(timezone.utc)
        return task

    def delete_task(self, task_id: UUID) -> None:
        """任务删除"""
        if task_id not in _tasks:
            raise TaskNotFound(str(task_id))
        del _tasks[task_id]


todo_service = TodoService()
