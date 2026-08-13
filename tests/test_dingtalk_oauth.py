"""Tests for the DingTalk OAuth endpoints (app/dingtalk/router.py).

The OAuth flow: user connects their own DingTalk via browser authorization.

    GET /api/v1/dingtalk/authorize → { authorize_url, state }   (JWT identity)
    GET /api/v1/dingtalk/callback?code=..&state=..              (DingTalk calls)

The callback has NO JWT — identity comes from the one-time ``state``. These
tests mock the HTTP seams (exchange_code_for_token / get_user_unionid /
build_authorize_url) and the store write so no network is touched.
"""

import pytest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore

from app.common.dependencies import get_current_user_id
from app.dingtalk import router as dingtalk_router
from app.dingtalk.router import dingtalkRouter


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(dingtalkRouter, prefix="/api/v1")
    # The /callback endpoint resolves store via Depends from app.state.app_context.
    app.state.app_context = SimpleNamespace(store=InMemoryStore(), graph=None, pool=None)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    return TestClient(app)


def _set_user(client, user_id):
    client.app.dependency_overrides[get_current_user_id] = lambda: user_id


# ── authorize ───────────────────────────────────────────────────────────


def test_authorize_returns_url(client, monkeypatch):
    captured = {}

    def fake_build(user_id):
        captured["user_id"] = user_id
        return {"authorize_url": "https://login.dingtalk.com/oauth2/auth?...", "state": "abc123"}

    monkeypatch.setattr(dingtalk_router, "build_authorize_url", fake_build)

    resp = client.get("/api/v1/dingtalk/authorize")

    assert resp.status_code == 200
    body = resp.json()
    assert body["authorize_url"].startswith("https://login.dingtalk.com")
    assert body["state"] == "abc123"
    assert captured["user_id"] == "user-1"


def test_authorize_scoped_to_caller(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        dingtalk_router,
        "build_authorize_url",
        lambda user_id: captured.setdefault("ids", []).append(user_id) or {"authorize_url": "u", "state": "s"},
    )
    _set_user(client, "user-42")
    client.get("/api/v1/dingtalk/authorize")
    assert captured["ids"] == ["user-42"]


def test_authorize_anonymous_blocked(client):
    _set_user(client, "default")
    resp = client.get("/api/v1/dingtalk/authorize")
    assert resp.status_code == 400


# ── callback ────────────────────────────────────────────────────────────


def test_callback_exchanges_code_and_stores_token(client, monkeypatch):
    """Valid state → exchange code → store token for the bound user."""
    # Prime a state bound to user-7 (as if authorize() ran earlier).
    monkeypatch.setattr(dingtalk_router, "_verify_state", lambda state: "user-7")

    async def fake_exchange(code):
        assert code == "auth_code_xyz"
        return {"access_token": "AT", "refresh_token": "RT", "expire_in": 7200, "scope": "openid"}

    async def fake_unionid(at):
        return "union-123"

    monkeypatch.setattr(dingtalk_router, "exchange_code_for_token", fake_exchange)
    monkeypatch.setattr(dingtalk_router, "get_user_unionid", fake_unionid)

    stored = {}
    marked = []

    def fake_put(store, user_id, token):
        stored["user_id"] = user_id
        stored["token"] = token

    async def fake_mark(user_id):
        marked.append(user_id)

    monkeypatch.setattr(dingtalk_router, "put_dingtalk_token", fake_put)
    monkeypatch.setattr(dingtalk_router, "mark_user_connected", fake_mark)

    resp = client.get("/api/v1/dingtalk/callback", params={"code": "auth_code_xyz", "state": "s"})

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "钉钉连接成功" in resp.text  # 友好 HTML 页面
    assert stored["user_id"] == "user-7"
    assert stored["token"]["access_token"] == "AT"
    assert stored["token"]["refresh_token"] == "RT"
    assert stored["token"]["union_id"] == "union-123"
    # 回调同步内存注册表 enabled=True(mark_user_connected 被调用)
    assert marked == ["user-7"]


def test_callback_invalid_state_rejected(client, monkeypatch):
    """Invalid/expired state → 400, no token exchange, no store write."""
    from fastapi import HTTPException

    monkeypatch.setattr(
        dingtalk_router,
        "_verify_state",
        lambda state: (_ for _ in ()).throw(HTTPException(status_code=400, detail="Invalid state")),
    )
    called = []

    async def fake_exchange(code):
        called.append(code)
        return {"access_token": "x"}

    monkeypatch.setattr(dingtalk_router, "exchange_code_for_token", fake_exchange)

    resp = client.get("/api/v1/dingtalk/callback", params={"code": "c", "state": "bad"})

    assert resp.status_code == 200  # 友好 HTML(即使失败也不裸 JSON 错误)
    assert "text/html" in resp.headers["content-type"]
    assert "连接失败" in resp.text
    assert called == []  # no exchange attempted


def test_callback_unionid_failure_still_stores_token(client, monkeypatch):
    """If userinfo (union_id) fails, token is still stored (best-effort)."""
    from fastapi import HTTPException

    monkeypatch.setattr(dingtalk_router, "_verify_state", lambda state: "user-9")

    async def fake_exchange(code):
        return {"access_token": "AT", "refresh_token": "RT"}

    async def fake_unionid(at):
        raise HTTPException(status_code=502, detail="userinfo failed")

    monkeypatch.setattr(dingtalk_router, "exchange_code_for_token", fake_exchange)
    monkeypatch.setattr(dingtalk_router, "get_user_unionid", fake_unionid)

    stored = {}
    monkeypatch.setattr(
        dingtalk_router,
        "put_dingtalk_token",
        lambda store, user_id, token: stored.update(user_id=user_id, token=token),
    )
    # 回调会同步内存注册表 enabled=True；测试环境没有配置 DingTalk runtime,
    # 所以和 test_callback_exchanges_code_and_stores_token 一样 mock 掉。
    marked = []

    async def fake_mark(user_id):
        marked.append(user_id)

    monkeypatch.setattr(dingtalk_router, "mark_user_connected", fake_mark)

    resp = client.get("/api/v1/dingtalk/callback", params={"code": "c", "state": "s"})

    assert resp.status_code == 200
    assert stored["user_id"] == "user-9"
    assert stored["token"]["union_id"] == ""  # empty union_id, token kept


# ── exchange_code_for_token 字段标准化 ──────────────────────────────────


def test_exchange_code_for_token_normalizes_camelcase(monkeypatch):
    """钉钉返回驼峰 accessToken/refreshToken → 标准化为 access_token 等。

    真实钉钉响应(见 oauth.py 注释): {"corpId":..., "accessToken":"...",
    "refreshToken":"...", "expireIn":7200}。若按下划线字段判断会误判失败。
    """
    from app.dingtalk import oauth as oauth_module

    calls = []

    class FakeResp:
        status_code = 200
        text = '{"corpId":"dingx","accessToken":"AT","refreshToken":"RT","expireIn":7200}'

        def json(self):
            return {"corpId": "dingx", "accessToken": "AT", "refreshToken": "RT", "expireIn": 7200}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            calls.append((url, json))
            return FakeResp()

    monkeypatch.setattr(oauth_module.httpx, "AsyncClient", FakeClient)
    # 配置 oauth_enabled 为 True(需要 client_id/secret/redirect)
    monkeypatch.setattr(
        oauth_module,
        "get_auth_config",
        lambda: __import__("types").SimpleNamespace(
            DINGTALK_CLIENT_ID="cid",
            DINGTALK_CLIENT_SECRET="sec",
            DINGTALK_REDIRECT_URI="https://x/cb",
            DINGTALK_SCOPE="openid corpid",
            dingtalk_oauth_enabled=True,
        ),
    )

    import asyncio

    result = asyncio.run(oauth_module.exchange_code_for_token("code1"))

    assert result["access_token"] == "AT"
    assert result["refresh_token"] == "RT"
    assert result["expire_in"] == 7200
    assert calls[0][1]["clientId"] == "cid"  # 请求体用驼峰 clientId


def test_exchange_code_for_token_rejects_missing_token(monkeypatch):
    """钉钉 200 但无 accessToken/access_token → 视为失败。"""
    from app.dingtalk import oauth as oauth_module

    class FakeResp:
        status_code = 200
        text = '{"error":"no token"}'

        def json(self):
            return {"error": "no token"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            return FakeResp()

    monkeypatch.setattr(oauth_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        oauth_module,
        "get_auth_config",
        lambda: __import__("types").SimpleNamespace(
            DINGTALK_CLIENT_ID="cid",
            DINGTALK_CLIENT_SECRET="sec",
            DINGTALK_REDIRECT_URI="https://x/cb",
            DINGTALK_SCOPE="openid corpid",
            dingtalk_oauth_enabled=True,
        ),
    )

    from fastapi import HTTPException
    import asyncio

    with pytest.raises(HTTPException) as exc:
        asyncio.run(oauth_module.exchange_code_for_token("code1"))
    assert exc.value.status_code == 502
