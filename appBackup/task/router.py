"""任务域路由"""
from uuid import UUID

from fastapi import APIRouter, Depends

from appBackup.task.schemas import (
    TaskAssign,
    TaskCreate,
    TaskResponse,
    TaskUpdate,
    TodoListCreate,
    TodoListDetailResponse,
    TodoListResponse,
    TodoListUpdate,
)
from appBackup.task.dependencies import get_todo_service, valid_todo_list_id, valid_task_id
from appBackup.task.service import TodoService

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ==================== 清单操作 ====================


@router.post("/lists", response_model=TodoListResponse, status_code=201)
async def create_todo_list(
    data: TodoListCreate,
    svc: TodoService = Depends(get_todo_service),
):
    """清单创建"""
    return svc.create_list(data)


@router.get("/lists", response_model=list[TodoListResponse])
async def get_todo_lists(
    svc: TodoService = Depends(get_todo_service),
):
    """获取所有清单"""
    return svc.get_lists()


@router.get("/lists/{list_id}", response_model=TodoListDetailResponse)
async def get_todo_list(
    todo_list: dict = Depends(valid_todo_list_id),
    svc: TodoService = Depends(get_todo_service),
):
    """获取清单详情（含任务列表）"""
    tasks = svc.get_tasks_by_list(todo_list["id"])
    return {**todo_list, "task_count": len(tasks), "tasks": tasks}


@router.put("/lists/{list_id}", response_model=TodoListResponse)
async def update_todo_list(
    data: TodoListUpdate,
    todo_list: dict = Depends(valid_todo_list_id),
    svc: TodoService = Depends(get_todo_service),
):
    """修改清单"""
    return svc.update_list(todo_list["id"], data)


@router.delete("/lists/{list_id}", status_code=204)
async def delete_todo_list(
    todo_list: dict = Depends(valid_todo_list_id),
    svc: TodoService = Depends(get_todo_service),
):
    """删除清单"""
    svc.delete_list(todo_list["id"])


# ==================== 任务操作 ====================


@router.post("/lists/{list_id}/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    data: TaskCreate,
    todo_list: dict = Depends(valid_todo_list_id),
    svc: TodoService = Depends(get_todo_service),
):
    """在清单下创建任务"""
    return svc.create_task(todo_list["id"], data)


@router.get("/lists/{list_id}/tasks", response_model=list[TaskResponse])
async def get_tasks_by_list(
    todo_list: dict = Depends(valid_todo_list_id),
    svc: TodoService = Depends(get_todo_service),
):
    """任务读取 — 获取清单下所有任务"""
    return svc.get_tasks_by_list(todo_list["id"])


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task: dict = Depends(valid_task_id),
):
    """任务读取 — 获取单个任务"""
    return task


@router.post("/tasks/{task_id}/assign", response_model=TaskResponse)
async def assign_task(
    data: TaskAssign,
    task: dict = Depends(valid_task_id),
    svc: TodoService = Depends(get_todo_service),
):
    """任务指派"""
    return svc.assign_task(task["id"], data.assignee)


@router.put("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    data: TaskUpdate,
    task: dict = Depends(valid_task_id),
    svc: TodoService = Depends(get_todo_service),
):
    """任务修改"""
    return svc.update_task(task["id"], data)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(
    task: dict = Depends(valid_task_id),
    svc: TodoService = Depends(get_todo_service),
):
    """任务删除"""
    svc.delete_task(task["id"])
