"""WeChat Mini Program one-tap login.

Flow (all behind the backend proxy so the mini-program never calls external
domains over ``wx.request``):

    wx.login() → code
      → POST /api/v1/auth/wechat-login { code }
      → _code2session_http():  code → openid  (WeChat jscode2session)
      → deterministic pseudo email + derived password from openid
      → GoTrue password grant → session            (returning user, 1 round-trip)
      → on 400: admin create email-confirmed user, then grant again (new user)

The returned session is a standard GoTrue ``{ access_token, refresh_token,
user }`` — the frontend's ``applySession()`` consumes it unchanged, and
``verify_supabase_token`` (aud="authenticated", ES256) validates it with zero
changes.

Security notes:
  - ``code`` is single-use (~5 min) — WeChat enforces this, preventing replay.
  - ``openid`` is a deterministic mapping key, not a credential: deriving the
    password also needs the server secret.
  - pseudo email/password never leave the server, never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

import httpx
from fastapi import HTTPException

from ainote.config.auth_config import AuthConfig, get_auth_config

logger = logging.getLogger(__name__)

WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"
WECHAT_EMAIL_DOMAIN = "wechat.local"

# WeChat code2session error codes → HTTP status mapping.
_WECHAT_HTTP_BY_ERRCODE = {
    40029: 401,   # invalid code
    40163: 401,   # code already used
    45011: 429,   # api rate limit
    40013: 503,   # invalid appid (placeholder not configured)
}


class UserAlreadyExists(Exception):
    """Raised when admin create hits an already-registered email."""


# ── WeChat code → openid ──────────────────────────────────────────────


async def _code2session_http(cfg: AuthConfig, code: str) -> str:
    """Exchange a WeChat login ``code`` for ``openid``.

    Raises ``HTTPException`` on failure (401 invalid/used code, 429 rate
    limit, 502 upstream unreachable). Returns the ``openid`` on success.
    """
    params = {
        "appid": cfg.WECHAT_APPID,
        "secret": cfg.WECHAT_APPSECRET,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(WECHAT_CODE2SESSION_URL, params=params)
            data = resp.json()
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("weixin code2session unreachable: %s", exc)
        raise HTTPException(status_code=502, detail="WeChat API unreachable")

    errcode = data.get("errcode", 0)
    if errcode:
        status = _WECHAT_HTTP_BY_ERRCODE.get(errcode, 502)
        detail = data.get("errmsg") or f"weixin error {errcode}"
        logger.warning("weixin code2session failed: %s", detail)
        raise HTTPException(status_code=status, detail=f"WeChat login failed: {detail}")

    openid = data.get("openid")
    if not openid:
        logger.warning("weixin code2session returned no openid")
        raise HTTPException(status_code=502, detail="WeChat login failed: no openid")
    return openid


# ── Deterministic pseudo email / derived password ─────────────────────


def wechat_email(cfg: AuthConfig, openid: str) -> str:
    """Deterministic pseudo email for a WeChat user.

    ``wechat_<sha256(appid:openid)[:20]>@wechat.local`` — stable for the same
    user, and includes the appid so different mini-programs never collide.
    """
    digest = hashlib.sha256(f"{cfg.WECHAT_APPID}:{openid}".encode()).hexdigest()[:20]
    return f"wechat_{digest}@{WECHAT_EMAIL_DOMAIN}"


def wechat_password(cfg: AuthConfig, openid: str) -> str:
    """Deterministic password derived from ``openid`` + server secret.

    ``HMAC-SHA256(secret, "wechat-user:"+openid)`` base64-urlencoded, 32 chars
    (uppercase + lowercase + digits — satisfies GoTrue password policy).
    """
    secret = cfg.WECHAT_PASSWORD_SECRET or cfg.WECHAT_APPSECRET
    raw = hmac.new(secret.encode(), f"wechat-user:{openid}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw)[:32].decode()


# ── GoTrue calls (test seams) ─────────────────────────────────────────


async def _gotrue_password_grant(cfg: AuthConfig, email: str, password: str) -> dict | None:
    """Try a password grant against GoTrue. Returns the session or ``None``.

    ``None`` means "invalid credentials" (user doesn't exist yet, or password
    mismatch) — the caller decides. Other upstream errors raise HTTPException.
    """
    url = f"{cfg.SUPABASE_URL.strip().rstrip('/')}/auth/v1/token?grant_type=password"
    headers = {
        "apikey": cfg.SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {cfg.SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }
    body = {"email": email, "password": password}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.RequestError as exc:
        logger.warning("GoTrue password grant unreachable: %s", exc)
        raise HTTPException(status_code=502, detail="Auth upstream unreachable")

    if resp.status_code == 400:
        return None  # invalid login credentials
    if resp.status_code >= 400:
        logger.warning("GoTrue password grant failed: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=502, detail="Auth upstream error")
    return resp.json()


async def _gotrue_admin_create(
    cfg: AuthConfig,
    *,
    email: str,
    password: str,
    user_metadata: dict,
) -> dict:
    """Create an email-confirmed user via the GoTrue admin API (service_role).

    ``email_confirm=True`` bypasses email verification (pseudo email can't
    receive a confirmation). Raises ``UserAlreadyExists`` on 422 conflict.
    """
    url = f"{cfg.SUPABASE_URL.strip().rstrip('/')}/auth/v1/admin/users"
    headers = {
        "apikey": cfg.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {cfg.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "email": email,
        "password": password,
        "email_confirm": True,
        "user_metadata": user_metadata,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.RequestError as exc:
        logger.warning("GoTrue admin create unreachable: %s", exc)
        raise HTTPException(status_code=502, detail="Auth upstream unreachable")

    if resp.status_code == 422:
        raise UserAlreadyExists(email)
    if resp.status_code >= 400:
        logger.warning("GoTrue admin create failed: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=502, detail="Auth upstream error")
    return resp.json()


# ── Orchestration ─────────────────────────────────────────────────────


async def wechat_login(code: str) -> dict:
    """Exchange a WeChat login code for a standard GoTrue session."""
    cfg = get_auth_config()
    if not cfg.wechat_enabled:
        raise HTTPException(status_code=503, detail="WeChat login not configured on server")

    openid = await _code2session_http(cfg, code)
    email = wechat_email(cfg, openid)
    password = wechat_password(cfg, openid)
    user_metadata = {"provider": "wechat", "openid": openid}

    # Returning user: a single password grant round-trip.
    session = await _gotrue_password_grant(cfg, email, password)
    if session is not None:
        return session

    # New user: create an email-confirmed account, then sign in.
    try:
        await _gotrue_admin_create(
            cfg, email=email, password=password, user_metadata=user_metadata
        )
    except UserAlreadyExists as exc:
        # Race or a mismatched derived password (e.g. APPSECRET rotated). v1:
        # fail loudly rather than silently issue a broken session.
        logger.error("WeChat user exists but password grant failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="WeChat login conflict — please retry or contact support",
        )

    session = await _gotrue_password_grant(cfg, email, password)
    if session is None:
        logger.error("WeChat login: admin create succeeded but grant failed for %s", email)
        raise HTTPException(status_code=500, detail="WeChat login failed to issue session")
    return session
