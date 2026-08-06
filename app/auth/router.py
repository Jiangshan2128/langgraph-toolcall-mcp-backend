"""Supabase GoTrue auth proxy.

Why this exists: the mini-program calls the backend over
``wx.cloud.callContainer`` (WeChat CloudBase private link), which bypasses
the ``request`` legal-domain whitelist. But the login/signup/refresh calls
previously went straight to ``https://<project>.supabase.co/auth/v1`` over
``wx.request`` — and that domain can't be added to the whitelist (Supabase is
a foreign service with no ICP filing), so the WeChat review/experience build
rejects it with "request url not in domain list".

This router re-exposes the GoTrue endpoints behind the backend, so the
frontend does *all* auth through the same callContainer channel. The backend
forwards to Supabase and returns the session verbatim. Identity still comes
from the ``Authorization`` header on business endpoints (verified against
Supabase JWKS), never from a client-supplied user_id.
"""

import httpx
from fastapi import APIRouter, HTTPException, Request

from ainote.config.auth_config import get_auth_config
from app.auth.schemas import WeChatLoginRequest
from app.auth.wechat_service import wechat_login

authRouter = APIRouter(prefix="/auth", tags=["auth"])

# GoTrue endpoints that take a JSON body and return JSON.
#   token    → /auth/v1/token?grant_type=password|refresh_token
#   signup   → /auth/v1/signup
#   logout   → /auth/v1/logout
_GOTRUE_PATHS = ("token", "signup", "logout")


def _gotrue_base() -> str:
    """Return the GoTrue base URL, or raise if auth isn't configured."""
    cfg = get_auth_config()
    if not cfg.SUPABASE_URL:
        raise HTTPException(status_code=503, detail="Auth not configured on server")
    return cfg.SUPABASE_URL.strip().rstrip("/") + "/auth/v1"


def _forward_headers(request: Request, cfg) -> dict[str, str]:
    """Build GoTrue headers: apikey + Authorization (if the caller sent one)."""
    headers = {"apikey": cfg.SUPABASE_ANON_KEY, "Content-Type": "application/json"}
    authz = request.headers.get("Authorization")
    if authz:
        headers["Authorization"] = authz
    return headers


# NOTE: this MUST be registered before the `/{path:path}` catch-all below,
# otherwise Starlette matches `/wechat-login` to the proxy and returns 404.
@authRouter.post("/wechat-login")
async def wechat_login_endpoint(body: WeChatLoginRequest):
    """WeChat one-tap login.

    Body: ``{"code": "<wx.login() code>"}``. Exchanges the code for an openid,
    maps it to a deterministic Supabase account, and returns a standard GoTrue
    session ``{ access_token, refresh_token, user }`` — same shape as email
    login, so the frontend applies it identically.
    """
    return await wechat_login(body.code)


@authRouter.post("/{path:path}")
async def auth_proxy(path: str, request: Request):
    """Forward a GoTrue call (token/signup/logout) to Supabase.

    The request body is forwarded verbatim; the Supabase session JSON is
    returned unchanged. Only the three known GoTrue actions are allowed.
    """
    if path not in _GOTRUE_PATHS:
        raise HTTPException(status_code=404, detail=f"Unknown auth endpoint: {path}")

    cfg = get_auth_config()
    url = f"{_gotrue_base()}/{path}"
    # token requires a grant_type query, e.g. ?grant_type=password
    if path == "token":
        grant_type = request.query_params.get("grant_type")
        if not grant_type:
            raise HTTPException(status_code=400, detail="token requires grant_type")
        url += f"?grant_type={grant_type}"

    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url, content=body, headers=_forward_headers(request, cfg)
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Auth upstream error: {exc}")

    # Return the GoTrue response verbatim (status + JSON body, when present).
    # Some GoTrue errors (e.g. logout with an invalid token → 403) return an
    # EMPTY body — do not crash parsing it; pass the status through instead.
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    try:
        return resp.json()
    except ValueError:
        return resp.text
