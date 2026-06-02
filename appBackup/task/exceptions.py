"""任务域异常"""
from fastapi import HTTPException, status


class TodoListNotFound(HTTPException):
    def __init__(self, list_id: str):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Todo list '{list_id}' not found",
        )


class TaskNotFound(HTTPException):
    def __init__(self, task_id: str):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found",
        )
