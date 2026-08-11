"""Request/response models for the per-user DingTalk toggle endpoints.

Credentials are stored per user (never shared) and are NEVER echoed back in
responses — ``GET /status`` returns only booleans / counts / names.
"""

from pydantic import BaseModel


class DingTalkCredentials(BaseModel):
    """Per-user DingTalk app credentials.

    ``client_id`` / ``client_secret`` are required to enable; the rest are
    optional and passed through to the MCP server env when provided.
    ``active_profiles`` is a list of DingTalk capability profiles (e.g.
    ``["todo", "contact"]``).
    """

    client_id: str | None = None
    client_secret: str | None = None
    agent_id: str | None = None
    robot_token: str | None = None
    active_profiles: list[str] | None = None


class DingTalkEnableRequest(BaseModel):
    """Optional body for ``POST /api/v1/dingtalk/enable``.

    Omit the body entirely to re-enable with previously stored credentials.
    """

    credentials: DingTalkCredentials | None = None
