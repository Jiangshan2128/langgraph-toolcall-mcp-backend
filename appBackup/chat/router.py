"""聊天域路由"""
from fastapi import APIRouter, Depends

from appBackup.chat.schemas import ChatRequest, ChatResponse
from appBackup.chat.dependencies import get_chat_chain
from appBackup.chat.service import chat

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat_endpoint(
    req: ChatRequest,
    chain=Depends(get_chat_chain),
):
    """与 AI 助手对话，可通过自然语言操作任务"""
    answer = await chat(req.message)
    return ChatResponse(answer=answer)
