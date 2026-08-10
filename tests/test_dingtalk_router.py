"""Tests for the per-user DingTalk toggle HTTP endpoints (app/dingtalk/router.py).

The admin gate is gone: ANY authenticated user manages their OWN DingTalk.
Identity comes from the ``Authorization``-derived dependency, never the body.
The anonymous ``"default"`` identity (a shared fallback) is blocked from
enabling.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ainote.agents.graph.dingtalk_runtime import DingTalkConfigError, DingTalkError
from app.common.dependencies import get_current_user_id
from app.dingtalk import router as dingtalk_router
from app.dingtalk.router import dingtalkRouter


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(dingtalkRouter, prefix="/api/v1")
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    return TestClient(app)


def _set_user(client, user_id):
    client.app.dependency_overrides[get_current_user_id] = lambda: user_id


# ── status ──────────────────────────────────────────────────────────────


def test_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        dingtalk_router,
        "get_status",
        lambda user_id: {
            "user_id": user_id,
            "enabled": False,
            "credentials_configured": False,
            "loaded_tools": 0,
            "tool_names": [],
            "last_error": None,
            "server": "dingtalk",
            "active_profiles": [],
        },
    )
    resp = client.get("/api/v1/dingtalk/status")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_status_scoped_to_caller(client, monkeypatch):
    """status is per-user — the endpoint passes the resolved user_id through."""
    captured = {}

    def fake_status(user_id):
        captured["user_id"] = user_id
        return {"enabled": False, "loaded_tools": 0}

    monkeypatch.setattr(dingtalk_router, "get_status", fake_status)
    _set_user(client, "user-99")
    client.get("/api/v1/dingtalk/status")
    assert captured["user_id"] == "user-99"


# ── enable ──────────────────────────────────────────────────────────────


def test_enable_with_credentials(client, monkeypatch):
    captured = {}

    async def fake_enable(user_id, credentials=None, *, persist=True):
        captured["user_id"] = user_id
        captured["credentials"] = credentials
        return {
            "enabled": True,
            "changed": True,
            "loaded_tools": 2,
            "tool_names": ["dingtalk_a", "dingtalk_b"],
        }

    monkeypatch.setattr(dingtalk_router, "enable_dingtalk", fake_enable)

    resp = client.post(
        "/api/v1/dingtalk/enable",
        json={
            "credentials": {
                "client_id": "cid",
                "client_secret": "sec",
                "active_profiles": ["todo"],
            }
        },
    )

    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert captured["user_id"] == "user-1"
    assert captured["credentials"]["client_id"] == "cid"
    assert captured["credentials"]["active_profiles"] == ["todo"]
    assert "client_secret" not in resp.json()  # never echo secrets in responses


def test_enable_without_body_uses_stored(client, monkeypatch):
    captured = {}

    async def fake_enable(user_id, credentials=None, *, persist=True):
        captured["credentials"] = credentials
        return {"enabled": True, "changed": False, "loaded_tools": 0, "tool_names": []}

    monkeypatch.setattr(dingtalk_router, "enable_dingtalk", fake_enable)

    resp = client.post("/api/v1/dingtalk/enable")

    assert resp.status_code == 200
    assert captured["credentials"] is None  # runtime reuses stored creds


def test_enable_anonymous_blocked(client, monkeypatch):
    """The shared 'default' identity may not enable (would affect all visitors)."""
    called = []

    async def fake_enable(user_id, credentials=None, *, persist=True):
        called.append(user_id)
        return {"enabled": True, "changed": True, "loaded_tools": 0, "tool_names": []}

    monkeypatch.setattr(dingtalk_router, "enable_dingtalk", fake_enable)
    _set_user(client, "default")

    resp = client.post(
        "/api/v1/dingtalk/enable",
        json={"credentials": {"client_id": "cid", "client_secret": "sec"}},
    )

    assert resp.status_code == 400
    assert called == []  # enable_dingtalk never invoked


def test_enable_config_error_returns_400(client, monkeypatch):
    async def fake_enable(user_id, credentials=None, *, persist=True):
        raise DingTalkConfigError("client_id and client_secret are required")

    monkeypatch.setattr(dingtalk_router, "enable_dingtalk", fake_enable)

    resp = client.post("/api/v1/dingtalk/enable")

    assert resp.status_code == 400
    assert "client_id" in resp.json()["detail"]


def test_enable_failure_returns_502(client, monkeypatch):
    async def fake_enable(user_id, credentials=None, *, persist=True):
        raise DingTalkError("no tools loaded")

    monkeypatch.setattr(dingtalk_router, "enable_dingtalk", fake_enable)

    resp = client.post("/api/v1/dingtalk/enable")

    assert resp.status_code == 502
    assert "no tools loaded" in resp.json()["detail"]


# ── disable ─────────────────────────────────────────────────────────────


def test_disable_endpoint(client, monkeypatch):
    captured = {}

    async def fake_disable(user_id, *, persist=True):
        captured["user_id"] = user_id
        return {"enabled": False, "changed": True, "loaded_tools": 0}

    monkeypatch.setattr(dingtalk_router, "disable_dingtalk", fake_disable)

    resp = client.post("/api/v1/dingtalk/disable")

    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert captured["user_id"] == "user-1"


# ── no admin gate ───────────────────────────────────────────────────────


def test_any_user_can_toggle_own_dingtalk(client, monkeypatch):
    """Non-admin users are allowed — per-user isolation replaces the admin gate."""
    called = []

    async def fake_enable(user_id, credentials=None, *, persist=True):
        called.append(user_id)
        return {"enabled": True, "changed": True, "loaded_tools": 1, "tool_names": ["x"]}

    monkeypatch.setattr(dingtalk_router, "enable_dingtalk", fake_enable)
    _set_user(client, "user-99")

    resp = client.post("/api/v1/dingtalk/enable", json={"credentials": {"client_id": "c", "client_secret": "s"}})

    assert resp.status_code == 200
    assert called == ["user-99"]
