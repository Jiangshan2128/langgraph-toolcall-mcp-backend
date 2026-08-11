from fastapi import APIRouter, HTTPException

from app.common.dependencies import CurrentUserIdDep, SessionIdQueryDep
from app.chat.schemas import TaskListResponse, TaskUpdateRequest
from app.chat.task_service import delete_all_tasks, delete_task, list_tasks, update_task


taskRouter = APIRouter(prefix="/tasks", tags=["tasks"])


@taskRouter.get("/list", response_model=TaskListResponse)
async def list_tasks_endpoint(user_id: CurrentUserIdDep) -> TaskListResponse:
    """获取当前用户的全部任务列表。"""
    try:
        tasks = list_tasks(user_id=user_id)
        return {"ok": True, "tasks": tasks}
    except Exception as e:
        _as_http_error(e)


@taskRouter.delete("", response_model=TaskListResponse)
async def delete_all_tasks_endpoint(
    user_id: CurrentUserIdDep,
    session_id: SessionIdQueryDep = None,
) -> TaskListResponse:
    """删除当前用户的全部任务，返回空任务列表。

    ``session_id`` 可选：传入时会在对应会话线程注入"全部删除"通知，防止 LLM
    下次基于旧历史误判并重加已删除任务。
    """
    try:
        tasks = delete_all_tasks(user_id=user_id, session_id=session_id)
        return {"ok": True, "tasks": tasks}
    except Exception as e:
        _as_http_error(e)


def _as_http_error(e: Exception) -> None:
    """Convert a service-layer exception to a FastAPI HTTPException.

    - ``KeyError`` → 404
    - Everything else → 500 (generic message; real detail logged, never sent
      to the client to avoid leaking internals).
    """
    import logging

    logging.getLogger(__name__).exception("Task operation failed: %s", e)
    if isinstance(e, KeyError):
        raise HTTPException(status_code=404, detail=str(e))
    raise HTTPException(status_code=500, detail="Internal server error")


@taskRouter.delete("/{key}", response_model=TaskListResponse)
async def delete_task_endpoint(
    key: str,
    user_id: CurrentUserIdDep,
    session_id: SessionIdQueryDep = None,
) -> TaskListResponse:
    """删除指定 task，返回更新后的完整任务列表。

    ``session_id`` 可选：传入时会在对应会话线程注入删除通知，防止 LLM
    下次误判并重加已删除任务。
    """
    try:
        tasks = delete_task(key, user_id=user_id, session_id=session_id)
        return {"ok": True, "tasks": tasks}
    except Exception as e:
        _as_http_error(e)


@taskRouter.patch("/{key}", response_model=TaskListResponse)
async def update_task_endpoint(
    key: str,
    request: TaskUpdateRequest,
    user_id: CurrentUserIdDep,
    session_id: SessionIdQueryDep = None,
) -> TaskListResponse:
    """更新指定 task 的部分字段，返回更新后的完整任务列表。

    ``session_id`` 可选：传入时会在对应会话线程注入更新通知。
    """
    try:
        tasks = update_task(key, user_id=user_id, updates=request.updates, session_id=session_id)
        return {"ok": True, "tasks": tasks}
    except Exception as e:
        _as_http_error(e)

