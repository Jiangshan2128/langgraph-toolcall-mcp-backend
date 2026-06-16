from fastapi.testclient import TestClient

from app.main import fastApi

client = TestClient(fastApi)


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
