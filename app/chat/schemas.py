from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(default="", description="用户消息")
    user_id: str = Field(default="default", description="用户标识")
    language: str | None = Field(default=None, description="音频语言，如 'zh', 'en'")


class ChatResponse(BaseModel):
    answer: str = Field(..., description="AI 自然语言回复")
    tasks: list[dict] = Field(default_factory=list, description="当前用户的任务列表")
    interrupt: dict | None = Field(
        default=None,
        description="存在时表示图执行被挂起，等待人工审批。前端应渲染审批卡片。",
    )


class ResumeRequest(BaseModel):
    """Request to resume a paused graph after human review.

    The ``decision`` field is passed directly as the ``resume`` payload
    to ``Command(resume=...)`` — its shape must match what the tool's
    ``interrupt()`` call expects.
    """

    user_id: str = Field(default="default", description="用户标识")
    decision: dict = Field(
        ...,
        description=(
            "人工决定，例如 "
            '{"approved": true, "rejected_keys": [], "edited_tasks": []} '
            "或 {\"approved\": false}"
        ),
    )


class TaskUpdateRequest(BaseModel):
    """Request body for patching a task."""

    updates: dict = Field(
        ...,
        description='要更新的字段，如 {"status": "done", "priority": "P0"}',
    )


class TaskListResponse(BaseModel):
    """Response payload for task list operations."""

    ok: bool = Field(default=True, description="操作是否成功")
    tasks: list[dict] = Field(default_factory=list, description="任务列表")
