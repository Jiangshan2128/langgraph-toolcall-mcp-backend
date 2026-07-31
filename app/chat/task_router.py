from fastapi import APIRouter, HTTPException

from app.common.dependencies import UserIdQueryDep
from app.chat.schemas import TaskListResponse, TaskUpdateRequest
from app.chat.task_service import delete_task, list_tasks, update_task


taskRouter = APIRouter(prefix="/tasks", tags=["tasks"])


@taskRouter.get("/list", response_model=TaskListResponse)
async def list_tasks_endpoint(
    user_id: UserIdQueryDep = "default",
) -> TaskListResponse:
    """获取当前用户的全部任务列表。"""
    try:
        tasks = list_tasks(user_id=user_id)
        return {"ok": True, "tasks": tasks}
    except Exception as e:
        _as_http_error(e)


def _as_http_error(e: Exception) -> None:
    """Convert a service-layer exception to a FastAPI HTTPException.

    - ``KeyError`` → 404
    - Everything else → 500
    """
    if isinstance(e, KeyError):
        raise HTTPException(status_code=404, detail=str(e))
    raise HTTPException(status_code=500, detail=str(e))


@taskRouter.delete("/{key}", response_model=TaskListResponse)
async def delete_task_endpoint(key: str, user_id: UserIdQueryDep = "default") -> TaskListResponse:
    """删除指定 task，返回更新后的完整任务列表。"""
    try:
        tasks = delete_task(key, user_id=user_id)
        return {"ok": True, "tasks": tasks}
    except Exception as e:
        _as_http_error(e)


@taskRouter.patch("/{key}", response_model=TaskListResponse)
async def update_task_endpoint(
    key: str,
    request: TaskUpdateRequest,
    user_id: UserIdQueryDep = "default",
) -> TaskListResponse:
    """更新指定 task 的部分字段，返回更新后的完整任务列表。"""
    try:
        tasks = update_task(key, user_id=user_id, updates=request.updates)
        return {"ok": True, "tasks": tasks}
    except Exception as e:
        _as_http_error(e)

