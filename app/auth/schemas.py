"""Auth request/response schemas."""

from pydantic import BaseModel, Field


class WeChatLoginRequest(BaseModel):
    """Request body for WeChat one-tap login.

    ``code`` is the single-use code from ``wx.login()`` (valid ~5 minutes).
    """

    code: str = Field(..., min_length=1, description="wx.login() one-time code")
