"""聊天域 Pydantic 模型"""
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户消息")


class ChatResponse(BaseModel):
    answer: str = Field(..., description="AI 回复")
