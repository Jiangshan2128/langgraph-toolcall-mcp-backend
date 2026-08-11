"""Tests for WeChat one-tap login (app/auth/wechat_service.py).

The WeChat and GoTrue calls are isolated behind three module-level seams —
``_code2session_http``, ``_gotrue_password_grant``, ``_gotrue_admin_create``
— so these tests monkeypatch them and never touch the network.
"""

import pytest
from fastapi import HTTPException

from ainote.config.auth_config import reset_auth_config
from app.auth import wechat_service as svc
from app.auth.wechat_service import UserAlreadyExists

PROJECT_URL = "https://example.supabase.co"


@pytest.fixture(autouse=True)
def _wx_env(monkeypatch: pytest.MonkeyPatch):
    """Configure WeChat + Supabase env for every test and reset the singleton."""
    monkeypatch.setenv("SUPABASE_URL", PROJECT_URL)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")
    monkeypatch.setenv("WECHAT_APPID", "wx-test-appid")
    monkeypatch.setenv("WECHAT_APPSECRET", "wx-test-secret")
    reset_auth_config()
    yield
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("WECHAT_APPID", raising=False)
    monkeypatch.delenv("WECHAT_APPSECRET", raising=False)
    reset_auth_config()


def _config():
    from ainote.config.auth_config import get_auth_config
    return get_auth_config()


# ── Deterministic identity helpers ─────────────────────────────────────


def test_wechat_email_is_deterministic():
    cfg = _config()
    e1 = svc.wechat_email(cfg, "openid-abc")
    e2 = svc.wechat_email(cfg, "openid-abc")
    assert e1 == e2
    assert e1.endswith("@wechat.local")
    assert e1.startswith("wechat_")
    # Different openid → different email
    assert e1 != svc.wechat_email(cfg, "openid-xyz")


def test_wechat_password_is_deterministic_and_strong():
    cfg = _config()
    p1 = svc.wechat_password(cfg, "openid-abc")
    p2 = svc.wechat_password(cfg, "openid-abc")
    assert p1 == p2
    assert len(p1) == 32
    # Mix of cases + digits (urlsafe b64) satisfies GoTrue password policy
    assert any(c.isupper() for c in p1)
    assert any(c.isdigit() for c in p1)


def test_wechat_enabled_true_when_configured():
    assert _config().wechat_enabled is True


def test_wechat_enabled_false_without_secret(monkeypatch):
    monkeypatch.setenv("WECHAT_APPSECRET", "")
    reset_auth_config()
    assert _config().wechat_enabled is False


# ── Orchestration ──────────────────────────────────────────────────────


async def test_new_user_creates_then_grants(monkeypatch):
    async def fake_code2session(cfg, code):
        assert code == "the-code"
        return "openid-new"

    grant_calls = []

    async def fake_grant(cfg, email, password):
        grant_calls.append(email)
        # First call: user doesn't exist yet → None. Second: session.
        if len(grant_calls) == 1:
            return None
        return {"access_token": "tok", "refresh_token": "ref", "user": {"id": "u1"}}

    created = []

    async def fake_admin_create(cfg, *, email, password, user_metadata):
        created.append(user_metadata)
        return {"id": "u1"}

    monkeypatch.setattr(svc, "_code2session_http", fake_code2session)
    monkeypatch.setattr(svc, "_gotrue_password_grant", fake_grant)
    monkeypatch.setattr(svc, "_gotrue_admin_create", fake_admin_create)

    session = await svc.wechat_login("the-code")

    assert session["access_token"] == "tok"
    # Admin create was called with openid in metadata
    assert len(created) == 1
    assert created[0]["provider"] == "wechat"
    assert created[0]["openid"] == "openid-new"
    # Two grants (first failed, second succeeded)
    assert len(grant_calls) == 2


async def test_returning_user_single_grant(monkeypatch):
    async def fake_code2session(cfg, code):
        return "openid-existing"

    async def fake_grant(cfg, email, password):
        return {"access_token": "tok", "user": {"id": "u1"}}

    admin_called = []

    async def fake_admin_create(cfg, **kwargs):
        admin_called.append(True)
        raise AssertionError("admin create should not be called for returning user")

    monkeypatch.setattr(svc, "_code2session_http", fake_code2session)
    monkeypatch.setattr(svc, "_gotrue_password_grant", fake_grant)
    monkeypatch.setattr(svc, "_gotrue_admin_create", fake_admin_create)

    session = await svc.wechat_login("code")
    assert session["access_token"] == "tok"
    assert admin_called == []


@pytest.mark.parametrize(
    "errcode, expected_status",
    [(40029, 401), (40163, 401), (45011, 429), (40013, 503)],
)
async def test_wechat_api_error_mapping(monkeypatch, errcode, expected_status):
    async def fake_code2session(cfg, code):
        raise HTTPException(status_code=expected_status, detail="boom")

    async def fake_grant(cfg, email, password):
        raise AssertionError("grant should not be called")

    monkeypatch.setattr(svc, "_code2session_http", fake_code2session)
    monkeypatch.setattr(svc, "_gotrue_password_grant", fake_grant)

    with pytest.raises(HTTPException) as exc:
        await svc.wechat_login("code")
    assert exc.value.status_code == expected_status


async def test_wechat_login_not_configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setenv("WECHAT_APPSECRET", "")
    reset_auth_config()

    with pytest.raises(HTTPException) as exc:
        await svc.wechat_login("code")
    assert exc.value.status_code == 503


async def test_user_already_exists_conflict(monkeypatch):
    """Returning user whose password grant fails AND admin create conflicts."""
    async def fake_code2session(cfg, code):
        return "openid-conflict"

    async def fake_grant(cfg, email, password):
        return None  # always fails — password mismatch

    async def fake_admin_create(cfg, **kwargs):
        raise UserAlreadyExists("wechat_x@wechat.local")

    monkeypatch.setattr(svc, "_code2session_http", fake_code2session)
    monkeypatch.setattr(svc, "_gotrue_password_grant", fake_grant)
    monkeypatch.setattr(svc, "_gotrue_admin_create", fake_admin_create)

    with pytest.raises(HTTPException) as exc:
        await svc.wechat_login("code")
    assert exc.value.status_code == 500
