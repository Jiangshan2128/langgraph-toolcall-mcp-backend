"""Pydantic schemas for the transcription endpoint.

The SSE stream uses named events (event: <name> / data: <payload>) and the
payloads are plain strings or JSON strings, so these models are only used for
non-streaming documentation / future typed responses. Kept for parity with the
chat module layout.
"""

from pydantic import BaseModel, Field


class TranscriptionStyleSpec(BaseModel):
    """Optional future: a typed body for style selection. Currently passed as form fields."""

    style: str = Field(default="default", description="default | bullets | meeting | concise")
    language: str | None = Field(default=None, description="ISO-639-1 hint, e.g. 'zh', 'en'. Auto-detected if omitted.")
