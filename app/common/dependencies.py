"""FastAPI dependency injection utilities.

Provides reusable Annotated type aliases for common endpoint parameters.
Use these in router handlers instead of raw ``Form()`` / ``Query()`` defaults::

    from app.common.dependencies import SessionIdFormDep

    @router.post("")
    async def chat(session_id: SessionIdFormDep):
        ...
"""

from typing import Annotated, Any

from fastapi import Depends, File, Form, Header, HTTPException, Query, UploadFile

from ainote.config.auth_config import get_auth_config
from app.common.token_service import verify_supabase_token


# ── Chat router dependencies (form fields) ─────────────────────────────

SessionIdFormDep = Annotated[str, Form(...)]
"""``session_id`` extracted from a multipart form field.  Required: the frontend
generates it, so it must be passed again to resume or continue a conversation.

``Form(...)`` marks it required so a missing field yields a clean 422
("Field required") instead of FastAPI passing ``...`` into validation and
crashing while serializing the error body."""

MessageFormDep = Annotated[str, Form(description="用户消息")]
"""``message`` extracted from a multipart form field."""

LanguageFormDep = Annotated[str | None, Form(description="音频语言，如 'zh', 'en'")]
"""Optional ``language`` from a multipart form field.  Default to ``None`` at the use site."""

AudioFileDep = Annotated[UploadFile | None, File(description="原始音频文件")]
"""Optional audio file uploaded via multipart.  Default to ``None`` at the use site."""


# ── Task router dependencies (query parameters) ────────────────────────

SessionIdQueryDep = Annotated[str, Query(description="会话标识（前端生成的随机数）")]
"""``session_id`` extracted from a query parameter (e.g. ``?session_id=abc``)."""


# ── Current user (Supabase JWT) ────────────────────────────────────────


def get_current_user_id(authorization: str = Header(default="")) -> str:
    """Resolve the caller's user id from the Supabase access token.

    The backend never trusts a ``user_id`` sent by the client — identity
    comes from ``Authorization: Bearer <access_token>``, verified with the
    Supabase JWT secret. The token's ``sub`` claim is the real user id.

    - No ``Authorization`` header          → anonymous ``"default"`` fallback
    - ``Bearer <token>`` that verifies     → the token's ``sub`` claim
    - Present but invalid / expired token  → 401 (forged identity rejected)
    - No Supabase URL configured           → ``"default"`` (auth skipped, dev)
    """
    if not get_auth_config().enabled:
        return "default"  # dev mode: no Supabase URL → skip auth
    if not authorization:
        return "default"  # anonymous caller (status-quo fallback)

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header (expected 'Bearer <token>')",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = verify_supabase_token(token.strip())
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


CurrentUserIdDep = Annotated[str, Depends(get_current_user_id)]
"""Current caller's user id, resolved from the Supabase access token.

Never take ``user_id`` from the request body/query — it can be forged.
Use this dependency on every route that needs the user's persisted data::

    from app.common.dependencies import CurrentUserIdDep

    @router.get("/list")
    async def list_tasks(user_id: CurrentUserIdDep):
        ...
"""


# ── Audio upload helper ────────────────────────────────────────────────


async def read_audio(file: Any | None) -> tuple[bytes | None, str | None]:
    """Read an optional ``UploadFile`` and return ``(bytes, filename)``.

    Both chat and chat_stream endpoints share this logic.  Call once at the
    top of each handler::

        audio_bytes, audio_filename = await read_audio(audio)
    """
    if file is None:
        return None, None
    return await file.read(), file.filename
