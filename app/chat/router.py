from fastapi import APIRouter

from app.chat.schemas import ChatRequest, ChatResponse
from app.chat.service import chat_llm


chatRouter = APIRouter(prefix="/chat", tags=["chat"])

@chatRouter.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    data = await chat_llm(request.message)
    return ChatResponse(answer=data["reply"], tasks=data["tasks"])