"""Job state-machine models for the polling chat flow.

A ``Job`` represents one background chat run. The frontend submits a message
and receives a ``job_id`` immediately, then polls ``GET /chat/jobs/{id}``
until the job reaches a terminal state. This keeps every HTTP request short
(well under CloudBase's 15s ``callContainer`` limit) while the LLM agent runs
for as long as it needs in the background.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    interrupt = "interrupt"  # waiting for a human decision (HITL approval)
    done = "done"
    failed = "failed"
    timeout = "timeout"


#: Statuses in which the job is still live (has an active runner or awaits a resume).
ACTIVE_STATUSES = (JobStatus.pending, JobStatus.running, JobStatus.interrupt)

#: Statuses from which the job will never transition again.
TERMINAL_STATUSES = (JobStatus.done, JobStatus.failed, JobStatus.timeout)


class Job(BaseModel):
    """One background chat run.

    Lifecycle: ``pending -> running -> (interrupt -> running)* -> done``,
    with ``failed`` / ``timeout`` reachable from any non-terminal state.

    ``interrupt`` holds the HITL approval payload while ``status == interrupt``;
    ``result`` is the final ``{reply, tasks}`` dict when ``status == done``.
    Timestamps are UTC ISO strings so the value is JSON-serializable for the
    LangGraph store (Postgres / InMemory).
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str = "default"
    session_id: str = ""
    status: JobStatus = JobStatus.pending
    message: str = ""
    has_audio: bool = False
    result: Optional[dict] = None
    interrupt: Optional[dict] = None
    error: Optional[str] = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    expires_at: str = Field(default_factory=_now_iso)


class JobResumeRequest(BaseModel):
    """Request body for resuming a job paused at HITL."""

    decision: dict = Field(
        ...,
        description=(
            "Human decision, same shape as the resume payload: "
            '{"approved": true, "rejected_keys": [], "edited_tasks": []} '
            "or {\"approved\": false}"
        ),
    )
