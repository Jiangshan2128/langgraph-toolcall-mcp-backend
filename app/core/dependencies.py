"""FastAPI dependency injection utilities.

Provides reusable Annotated type aliases for common endpoint parameters.
Use these in router handlers instead of raw ``Form()`` / ``Query()`` defaults::

    from app.core.dependencies import UserIdFormDep

    @router.post("")
    async def chat(user_id: UserIdFormDep = "default"):
        ...
"""

from typing import Annotated

from fastapi import File, Form, Query, UploadFile


# ── Chat router dependencies (form fields) ─────────────────────────────

UserIdFormDep = Annotated[str, Form(description="用户标识")]
"""``user_id`` extracted from a multipart form field."""

MessageFormDep = Annotated[str, Form(description="用户消息")]
"""``message`` extracted from a multipart form field."""

LanguageFormDep = Annotated[str | None, Form(description="音频语言，如 'zh', 'en'")]
"""Optional ``language`` from a multipart form field.  Default to ``None`` at the use site."""

AudioFileDep = Annotated[UploadFile | None, File(description="原始音频文件")]
"""Optional audio file uploaded via multipart.  Default to ``None`` at the use site."""


# ── Task router dependencies (query parameters) ────────────────────────

UserIdQueryDep = Annotated[str, Query(description="用户标识")]
"""``user_id`` extracted from a query parameter (e.g. ``?user_id=abc``)."""


# ── Audio upload helper ────────────────────────────────────────────────

from typing import Any

from fastapi import UploadFile


async def read_audio(file: Any | None) -> tuple[bytes | None, str | None]:
    """Read an optional ``UploadFile`` and return ``(bytes, filename)``.

    Both chat and chat_stream endpoints share this logic.  Call once at the
    top of each handler::

        audio_bytes, audio_filename = await read_audio(audio)
    """
    if file is None:
        return None, None
    return await file.read(), file.filename
