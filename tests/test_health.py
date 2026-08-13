from types import SimpleNamespace

from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore

from app.main import fastApi

# 测试环境不跑 lifespan(避免连接真实 DATABASE_URL)。直接注入一个内存 store,
# 让 /health 能看到可用 store(status="ok")。生产环境由 lifespan 安装真 context。
client = TestClient(fastApi)
client.app.state.app_context = SimpleNamespace(store=InMemoryStore(), pool=None)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert data["database"] in ("postgresql", "memory")


def test_root_returns_running_message():
    response = client.get("/")
    assert response.status_code == 200
    assert "AI Note Backend is running" in response.json()["message"]
