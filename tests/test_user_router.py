import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore

from app.common.dependencies import get_current_user_id
from app.user import service as user_service
from app.user.router import userRouter


@pytest.fixture
def store():
    """Isolated in-memory store, injected into the app context."""
    return InMemoryStore()


@pytest.fixture(autouse=True)
def _no_gotrue(monkeypatch):
    """Don't hit the real Supabase GoTrue admin API in router tests.

    Account deletion's GoTrue call is best-effort and covered separately at the
    service level (``test_delete_user_account_service_removes_store_and_calls_gotrue``);
    the router tests here only need to verify the store-clearing + 200 flow.
    Without this, these tests depend on a live network when
    ``SUPABASE_SERVICE_ROLE_KEY`` is configured.
    """

    async def _noop(cfg, user_id):
        return None

    monkeypatch.setattr(user_service, "_gotrue_admin_delete", _noop)


@pytest.fixture
def client(store):
    """Standalone app with just the user router; auth dependency overridden."""
    app = FastAPI()
    app.include_router(userRouter, prefix="/api/v1")
    # The router resolves store via Depends from app.state.app_context — the
    # same shape the lifespan installs in production.
    app.state.app_context = SimpleNamespace(store=store, graph=None, pool=None)
    app.dependency_overrides[get_current_user_id] = lambda: "user-42"
    return TestClient(app)


# ── Service level ──────────────────────────────────────────────────────


def test_update_user_profile_valid(store):
    result = user_service.update_user_profile(
        store,
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
    result = user_service.update_user_profile(store, "user-1", '{"name": "李四"}')
    assert result["name"] == "李四"
    assert result["gender"] is None


def test_update_user_profile_invalid_json_raises(store):
    with pytest.raises(user_service.ProfileValidationError):
        user_service.update_user_profile(store, "user-1", "{not json")


def test_update_user_profile_wrong_type_raises(store):
    with pytest.raises(user_service.ProfileValidationError):
        user_service.update_user_profile(store, "user-1", '{"name": 123}')


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


# ── DELETE /account ────────────────────────────────────────────────────


def test_delete_account_clears_all_user_data(client, store):
    """Deleting removes the user's profile + tasks + instructions; others untouched."""
    store.put(("profile", "user-42"), "user_profile", {"name": "张三"})
    store.put(("task", "user-42"), "k1", {"title": "扫地"})
    store.put(("task", "user-42"), "k2", {"title": "买菜"})
    store.put(("instructions", "user-42"), "user_instructions", {"x": 1})
    # Another user's data must survive.
    store.put(("task", "user-43"), "k3", {"title": "别人的任务"})

    resp = client.delete("/api/v1/user/account")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["deleted"] == 4

    assert store.search(("task", "user-42")) == []
    assert store.search(("profile", "user-42")) == []
    assert store.search(("instructions", "user-42")) == []
    assert len(store.search(("task", "user-43"))) == 1


def test_delete_account_idempotent_when_no_data(client):
    resp = client.delete("/api/v1/user/account")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0


def test_delete_account_rejects_anonymous(client):
    """Anonymous fallback (user_id='default') cannot delete an account."""
    from app.common.dependencies import get_current_user_id

    client.app.dependency_overrides[get_current_user_id] = lambda: "default"
    resp = client.delete("/api/v1/user/account")
    assert resp.status_code == 400


def test_delete_user_account_service_removes_store_and_calls_gotrue(store, monkeypatch):
    """Service level: store cleared + GoTrue admin delete invoked with the uid."""
    import asyncio

    store.put(("task", "user-9"), "k", {"title": "x"})
    called = []

    async def fake_delete(cfg, uid):
        called.append(uid)

    monkeypatch.setattr(user_service, "_gotrue_admin_delete", fake_delete)

    result = asyncio.run(user_service.delete_user_account(store, "user-9"))
    assert result["ok"] is True
    assert result["deleted"] == 1
    assert called == ["user-9"]
    assert store.search(("task", "user-9")) == []
