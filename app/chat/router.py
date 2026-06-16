from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.chat.schemas import ChatRequest, ChatResponse, DeleteTaskRequest, UpdateTaskRequest
from app.chat.service import chat_llm, chat_llm_stream, delete_task, update_task


chatRouter = APIRouter(prefix="/chat", tags=["chat"])


@chatRouter.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    print("123")
    data = await chat_llm(request.message, user_id=request.user_id)
    return ChatResponse(answer=data["reply"], tasks=data["tasks"])

@chatRouter.post("/stream")
async def chat_stream(request: ChatRequest):
    print("123")
    print(request)
    return EventSourceResponse(chat_llm_stream(request.message, user_id=request.user_id))


@chatRouter.delete("/task")
async def delete_task_endpoint(request: DeleteTaskRequest):
    """删除指定 task。"""
    try:
        delete_task(request.key, user_id=request.user_id)
        return {"ok": True, "message": f"Task '{request.key}' deleted"}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@chatRouter.patch("/task")
async def update_task_endpoint(request: UpdateTaskRequest):
    """更新指定 task 的部分字段。"""
    try:
        updated = update_task(request.key, user_id=request.user_id, updates=request.updates)
        return {"ok": True, "task": updated}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
