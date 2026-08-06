"""Tests for the DingTalk toggle HTTP endpoints (app/dingtalk/router.py)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.dependencies import get_current_user_id
from app.dingtalk import router as dingtalk_router
from app.dingtalk.router import dingtalkRouter


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(dingtalkRouter, prefix="/api/v1")
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    return TestClient(app)


def test_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        dingtalk_router,
        "get_status",
        lambda: {"enabled": False, "loaded_tools": 0, "tool_names": [], "last_error": None, "server": "dingtalk"},
    )
    resp = client.get("/api/v1/dingtalk/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert "tool_names" in body


def test_enable_endpoint(client, monkeypatch):
    async def fake_enable():
        return {"enabled": True, "changed": True, "loaded_tools": 3}

    monkeypatch.setattr(dingtalk_router, "enable_dingtalk", fake_enable)
    resp = client.post("/api/v1/dingtalk/enable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def test_enable_failure_returns_502(client, monkeypatch):
    async def fake_enable():
        raise dingtalk_router.DingTalkError("no tools")

    monkeypatch.setattr(dingtalk_router, "enable_dingtalk", fake_enable)
    resp = client.post("/api/v1/dingtalk/enable")
    assert resp.status_code == 502
    assert "no tools" in resp.json()["detail"]


def test_disable_endpoint(client, monkeypatch):
    async def fake_disable():
        return {"enabled": False, "changed": True, "loaded_tools": 0}

    monkeypatch.setattr(dingtalk_router, "disable_dingtalk", fake_disable)
    resp = client.post("/api/v1/dingtalk/disable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
