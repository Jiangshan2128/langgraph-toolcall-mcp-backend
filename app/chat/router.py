from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.chat.schemas import ChatRequest, ChatResponse
from app.chat.service import chat_llm, chat_llm_stream


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
