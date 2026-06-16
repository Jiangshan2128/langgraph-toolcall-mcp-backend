from fastapi import APIRouter, HTTPException, Query

from app.tasks.schemas import TaskUpdateRequest
from app.tasks.service import delete_task, update_task


taskRouter = APIRouter(prefix="/tasks", tags=["tasks"])


@taskRouter.delete("/{key}")
async def delete_task_endpoint(key: str, user_id: str = Query(default="default", description="用户标识")):
    """删除指定 task，返回更新后的完整任务列表。"""
    try:
        tasks = delete_task(key, user_id=user_id)
        return {"ok": True, "tasks": tasks}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@taskRouter.patch("/{key}")
async def update_task_endpoint(
    key: str,
    request: TaskUpdateRequest,
    user_id: str = Query(default="default", description="用户标识"),
):
    """更新指定 task 的部分字段，返回更新后的完整任务列表。"""
    try:
        tasks = update_task(key, user_id=user_id, updates=request.updates)
        return {"ok": True, "tasks": tasks}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
