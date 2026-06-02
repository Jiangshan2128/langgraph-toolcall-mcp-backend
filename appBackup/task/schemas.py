"""任务域 Pydantic 模型"""
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


# ---------- 枚举 ----------
class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


# ---------- 清单 (TodoList) ----------
class TodoListCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="清单标题")
    description: str | None = Field(None, max_length=1000, description="清单描述")


class TodoListUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200, description="清单标题")
    description: str | None = Field(None, max_length=1000, description="清单描述")


class TodoListResponse(BaseModel):
    id: UUID
    title: str
    description: str | None
    task_count: int = 0
    created_at: datetime
    updated_at: datetime


class TodoListDetailResponse(TodoListResponse):
    tasks: list["TaskResponse"] = []


# ---------- 任务 (Task) ----------
class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="任务标题")
    description: str | None = Field(None, max_length=1000, description="任务描述")
    assignee: str | None = Field(None, max_length=100, description="指派人")


class TaskAssign(BaseModel):
    assignee: str = Field(..., min_length=1, max_length=100, description="指派人")


class TaskUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200, description="任务标题")
    description: str | None = Field(None, max_length=1000, description="任务描述")
    assignee: str | None = Field(None, max_length=100, description="指派人")
    status: TaskStatus | None = Field(None, description="任务状态")


class TaskResponse(BaseModel):
    id: UUID
    todo_list_id: UUID
    title: str
    description: str | None
    assignee: str | None
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
