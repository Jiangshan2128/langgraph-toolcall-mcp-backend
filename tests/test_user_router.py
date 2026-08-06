import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore

from ainote.agents.graph import builder
from app.common.dependencies import get_current_user_id
from app.user import service as user_service
from app.user.router import userRouter


@pytest.fixture
def store(monkeypatch):
    """Isolated in-memory store, swapped in for the shared ``builder.store``."""
    s = InMemoryStore()
    monkeypatch.setattr(builder, "store", s)
    return s


@pytest.fixture
def client(store):
    """Standalone app with just the user router; auth dependency overridden."""
    app = FastAPI()
    app.include_router(userRouter, prefix="/api/v1")
    app.dependency_overrides[get_current_user_id] = lambda: "user-42"
    return TestClient(app)


# ── Service level ──────────────────────────────────────────────────────


def test_update_user_profile_valid(store):
    result = user_service.update_user_profile(
        "user-1",
        json.dumps(
            {"name": "张三", "gender": "男", "job": "工程师", "location": "北京"}
        ),
    )
    assert result["name"] == "张三"
    assert result["job"] == "工程师"

    saved = store.get(("profile", "user-1"), "user_profile")
    assert saved is not None
    assert saved.value["name"] == "张三"


def test_update_user_profile_partial_uses_put_semantics(store):
    """PUT replaces the whole document: missing fields become None."""
    result = user_service.update_user_profile("user-1", '{"name": "李四"}')
    assert result["name"] == "李四"
    assert result["gender"] is None


def test_update_user_profile_invalid_json_raises():
    with pytest.raises(user_service.ProfileValidationError):
        user_service.update_user_profile("user-1", "{not json")


def test_update_user_profile_wrong_type_raises():
    with pytest.raises(user_service.ProfileValidationError):
        user_service.update_user_profile("user-1", '{"name": 123}')


# ── Router level ───────────────────────────────────────────────────────


def test_put_profile_via_router(client, store):
    resp = client.put(
        "/api/v1/user/profile",
        content=json.dumps({"name": "张三", "gender": "男"}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["profile"]["name"] == "张三"

    saved = store.get(("profile", "user-42"), "user_profile")
    assert saved is not None
    assert saved.value["name"] == "张三"


def test_put_profile_invalid_json_returns_422(client):
    resp = client.put("/api/v1/user/profile", content="{not json")
    assert resp.status_code == 422


def test_put_profile_wrong_type_returns_422(client):
    resp = client.put("/api/v1/user/profile", content='{"name": 123}')
    assert resp.status_code == 422


def test_put_profile_is_per_user(client, store):
    """The resolved user_id scopes the write — other users' profiles untouched."""
    client.put("/api/v1/user/profile", content='{"name": "张三"}')
    assert store.get(("profile", "user-42"), "user_profile") is not None
    assert store.get(("profile", "user-43"), "user_profile") is None


# ── GET /profile ────────────────────────────────────────────────────────


def test_get_profile_returns_none_when_unset(client):
    resp = client.get("/api/v1/user/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["profile"] is None


def test_get_profile_returns_stored_profile(client, store):
    """After PUT, GET returns the same profile."""
    client.put("/api/v1/user/profile", content='{"name": "张三", "gender": "男"}')
    resp = client.get("/api/v1/user/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["name"] == "张三"
    assert body["profile"]["gender"] == "男"


def test_get_profile_is_per_user(client, store):
    """GET scopes to the resolved user_id — other users see their own (or None)."""
    # user-42 writes a profile; user-43 (different dep) has none.
    client.put("/api/v1/user/profile", content='{"name": "张三"}')

    # Override the dep to a different user for the GET.
    from app.common.dependencies import get_current_user_id

    client.app.dependency_overrides[get_current_user_id] = lambda: "user-43"
    resp = client.get("/api/v1/user/profile")
    assert resp.json()["profile"] is None
