from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户消息")
    user_id: str = Field(default="default", description="用户标识")

class ChatResponse(BaseModel):
    answer: str = Field(..., description="AI 自然语言回复")
    tasks: list[dict] = Field(default_factory=list, description="当前用户的任务列表")