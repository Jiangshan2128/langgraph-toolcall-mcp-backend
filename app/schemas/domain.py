from datetime import datetime
from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, Field


class Profile(BaseModel):
    """Profile of the user the agent is chatting with."""

    name: Optional[str] = Field(default=None, description="The user's name")
    location: Optional[str] = Field(default=None, description="The user's location")
    role: Optional[str] = Field(default=None, description="The user's role or job")
    connections: list[str] = Field(
        default_factory=list,
        description="People, teams, or groups the user works with",
    )
    preferences: list[str] = Field(
        default_factory=list,
        description="Preferences for task planning and communication",
    )


class Task(BaseModel):
    """A task the user wants to track."""

    title: str = Field(description="The task title")
    description: Optional[str] = Field(default=None, description="Task details")
    assignee: Optional[str] = Field(default=None, description="Who should own the task")
    priority: Literal["P0", "P1", "P2"] = Field(
        default="P1", description="P0 = urgent today, P1 = important, P2 = routine"
    )
    time: str = Field(default="", description="when to start the task, e.g. 'today' or 'next week'")
    deadline: Optional[str] = Field(
        default=None,
        description="Deadline as YYYY-MM-DD or descriptive text",
    )
    pre_task: Optional[str] = Field(
        default=None,
        description="Prerequisite task title, if any",
    )
    status: Literal["not started", "in progress", "done", "archived"] = Field(
        default="not started", description="Current task status"
    )


class UpdateMemory(TypedDict):
    """Decision on what memory type to update."""

    update_type: Literal["task", "profile", "instructions"]
