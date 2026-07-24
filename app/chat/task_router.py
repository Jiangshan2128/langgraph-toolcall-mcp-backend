from fastapi import APIRouter, HTTPException

from app.common.dependencies import UserIdQueryDep
from app.chat.schemas import TaskUpdateRequest
from app.chat.task_service import delete_task, update_task


taskRouter = APIRouter(prefix="/tasks", tags=["tasks"])


def _as_http_error(e: Exception) -> None:
    """Convert a service-layer exception to a FastAPI HTTPException.

    - ``KeyError`` → 404
    - Everything else → 500
    """
    if isinstance(e, KeyError):
        raise HTTPException(status_code=404, detail=str(e))
    raise HTTPException(status_code=500, detail=str(e))


@taskRouter.delete("/{key}")
async def delete_task_endpoint(key: str, user_id: UserIdQueryDep = "default"):
    """删除指定 task，返回更新后的完整任务列表。"""
    try:
        tasks = delete_task(key, user_id=user_id)
        return {"ok": True, "tasks": tasks}
    except Exception as e:
        _as_http_error(e)


@taskRouter.patch("/{key}")
async def update_task_endpoint(
    key: str,
    request: TaskUpdateRequest,
    user_id: UserIdQueryDep = "default",
):
    """更新指定 task 的部分字段，返回更新后的完整任务列表。"""
    try:
        tasks = update_task(key, user_id=user_id, updates=request.updates)
        return {"ok": True, "tasks": tasks}
    except Exception as e:
        _as_http_error(e)

