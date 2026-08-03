from datetime import date

from pydantic import BaseModel, Field, field_validator

from ainote.tools.core.memory import Task


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


class TaskOut(Task):
    """A task as returned to the frontend (adds the store ``key``).

    Inherits every field and the loose date parser from the agent-side
    ``Task`` so the Swagger schema shows the real structure (``time`` as
    ``string(date)``) instead of an opaque dict.
    """

    key: str = Field(description="Store key, used for PATCH / DELETE")

    @field_validator("time", mode="before")
    @classmethod
    def _time_lenient(cls, v):
        """Never fail the whole list over one legacy ``time`` value.

        Explicitly call the inherited parser rather than relying on validator
        ordering. ``Task._parse_time`` returns a ``date`` when it parses, or
        the input unchanged when it can't (e.g. legacy "next week"). Drop the
        unparseable values to ``None`` instead of raising — a single bad value
        should not turn ``GET /tasks/list`` into a 500.
        """
        parsed = Task._parse_time(v)
        return parsed if isinstance(parsed, date) else None


class TaskListResponse(BaseModel):
    """Response payload for task list operations."""

    ok: bool = Field(default=True, description="操作是否成功")
    tasks: list[TaskOut] = Field(default_factory=list, description="任务列表")
