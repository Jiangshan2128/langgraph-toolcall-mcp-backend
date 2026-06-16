from pydantic import BaseModel, Field


class TaskUpdateRequest(BaseModel):
    """Request body for patching a task."""

    updates: dict = Field(
        ...,
        description="要更新的字段，如 {\"status\": \"done\", \"priority\": \"P0\"}",
    )
