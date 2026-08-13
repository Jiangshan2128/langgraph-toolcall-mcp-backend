"""Tests for the polling chat job state machine (app/jobs/).

Covers the durable store (create/get/lazy-expiry/find-active/prune), the
async runner (submit → running → interrupt → resume → done, plus conflict and
not-resumable guards), and the router endpoints via TestClient.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore

from app.common.dependencies import get_current_user_id
from app.jobs import runner
from app.jobs import store as job_store
from app.jobs.models import Job, JobStatus
from app.jobs.router import jobRouter


# ── Fake graphs ─────────────────────────────────────────────────────────


class _FakeInstantGraph:
    """ainvoke returns a clean result on every call (no interrupt)."""

    async def ainvoke(self, command, **kwargs):
        return SimpleNamespace(
            value={"messages": [AIMessage(content="ok")]},
            interrupts=[],
        )


class _FakeHITLGraph:
    """First turn interrupts, second turn (after resume) completes."""

    def __init__(self):
        self.turns = 0

    async def ainvoke(self, command, **kwargs):
        self.turns += 1
        if self.turns == 1:
            return SimpleNamespace(
                value={"messages": [AIMessage(content="pre")]},
                interrupts=[
                    SimpleNamespace(
                        value={"type": "task_update_approval", "proposed_updates": []}
                    )
                ],
            )
        return SimpleNamespace(
            value={"messages": [AIMessage(content="final reply")]},
            interrupts=[],
        )


async def _wait_for_status(store, user_id, job_id, status, timeout: float = 5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        item = store.get(("job", user_id), job_id)
        if item is not None and Job.model_validate(item.value).status == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} never reached {status}")


# ── Store ───────────────────────────────────────────────────────────────


def test_store_create_and_get():
    store = InMemoryStore()
    job = job_store.create_job(store, Job(user_id="u1", session_id="s1"), expires_in=1000)

    read = job_store.get_job(store, "u1", job.id)
    assert read is not None
    assert read.id == job.id
    assert read.status == JobStatus.pending
    assert datetime.fromisoformat(read.expires_at) > datetime.now(timezone.utc)


def test_store_get_missing_returns_none():
    store = InMemoryStore()
    assert job_store.get_job(store, "u1", "nope") is None


def test_store_lazy_expiry():
    store = InMemoryStore()
    job = job_store.create_job(
        store, Job(user_id="u1", session_id="s1", status=JobStatus.running),
        expires_in=1000,
    )
    # Backdate expires_at to the past — an orphaned active job.
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    store.put(("job", "u1"), job.id, {**job.model_dump(mode="json"), "expires_at": past})

    read = job_store.get_job(store, "u1", job.id)
    assert read.status == JobStatus.timeout


def test_find_active_job_ignores_expired():
    """An expired active job (orphaned) does not block a new submit."""
    store = InMemoryStore()
    stale = job_store.create_job(
        store, Job(user_id="u1", session_id="s1", status=JobStatus.running),
        expires_in=1000,
    )
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    store.put(("job", "u1"), stale.id, {**stale.model_dump(mode="json"), "expires_at": past})

    assert job_store.find_active_job(store, "u1", "s1") is None
    # The stale job was lazily settled to timeout.
    settled = job_store.get_job(store, "u1", stale.id)
    assert settled.status == JobStatus.timeout


def test_find_active_job():
    store = InMemoryStore()
    active = job_store.create_job(
        store, Job(user_id="u1", session_id="s1"), expires_in=1000
    )
    job_store.create_job(store, Job(user_id="u1", session_id="s2"), expires_in=1000)
    job_store.create_job(
        store,
        Job(user_id="u1", session_id="s1", status=JobStatus.done),
        expires_in=1000,
    )

    found = job_store.find_active_job(store, "u1", "s1")
    assert found is not None
    assert found.id == active.id
    # Different user / different session are not conflicts
    assert job_store.find_active_job(store, "u1", "s2") is not None
    assert job_store.find_active_job(store, "u2", "s1") is None


def test_prune_old_jobs():
    store = InMemoryStore()
    old = job_store.create_job(
        store, Job(user_id="u1", session_id="s1", status=JobStatus.done),
        expires_in=1000,
    )
    past = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    store.put(("job", "u1"), old.id, {**old.model_dump(mode="json"), "created_at": past})
    fresh = job_store.create_job(
        store, Job(user_id="u1", session_id="s2", status=JobStatus.done),
        expires_in=1000,
    )

    removed = job_store.prune_old_jobs(store, "u1", max_age_hours=24)
    assert removed == 1
    assert job_store.get_job(store, "u1", old.id) is None
    assert job_store.get_job(store, "u1", fresh.id) is not None


# ── Runner ──────────────────────────────────────────────────────────────


async def test_submit_runs_to_done():
    store = InMemoryStore()
    graph = _FakeInstantGraph()

    job = runner.submit(
        user_id="u1", session_id="s1", message="hi", store=store, graph=graph
    )
    assert job.status == JobStatus.pending

    await _wait_for_status(store, "u1", job.id, JobStatus.done)
    final = runner.get(store, "u1", job.id)
    assert final.result == {"reply": "ok", "tasks": []}


async def test_interrupt_then_resume_then_done():
    store = InMemoryStore()
    graph = _FakeHITLGraph()

    job = runner.submit(
        user_id="u1", session_id="s1", message="add task", store=store, graph=graph
    )

    await _wait_for_status(store, "u1", job.id, JobStatus.interrupt)
    paused = runner.get(store, "u1", job.id)
    assert paused.interrupt["type"] == "task_update_approval"

    runner.resume(store, "u1", job.id, {"approved": True})

    await _wait_for_status(store, "u1", job.id, JobStatus.done)
    final = runner.get(store, "u1", job.id)
    assert final.result["reply"] == "final reply"


async def test_active_job_conflict_rejects_second_submit():
    store = InMemoryStore()
    active = job_store.create_job(
        store, Job(user_id="u1", session_id="s1"), expires_in=1000
    )
    runner._live_jobs.add(active.id)  # simulate a live runner task in this process

    with pytest.raises(runner.ActiveJobConflict) as exc_info:
        runner.submit(
            user_id="u1",
            session_id="s1",
            message="second",
            store=store,
            graph=_FakeInstantGraph(),
        )
    assert exc_info.value.job_id == active.id
    runner._live_jobs.discard(active.id)


async def test_submit_settles_orphaned_active_job():
    """An active job whose runner died (restart) must not block a new submit.

    ``submit()`` settles it as ``timeout`` (releasing the per-session lock)
    and proceeds with the new job — same orphan handling as ``resume()``.
    """
    store = InMemoryStore()
    graph = _FakeInstantGraph()
    stale = job_store.create_job(
        store,
        Job(user_id="u1", session_id="s1", status=JobStatus.interrupt),
        expires_in=1000,
    )

    job = runner.submit(
        user_id="u1", session_id="s1", message="new", store=store, graph=graph
    )

    assert job.id != stale.id
    settled = runner.get(store, "u1", stale.id)
    assert settled.status == JobStatus.timeout
    # The stale job no longer holds the lock — the new job is the active one.
    assert job_store.find_active_job(store, "u1", "s1").id == job.id
    await _wait_for_status(store, "u1", job.id, JobStatus.done)


def test_resume_non_interrupt_raises():
    store = InMemoryStore()
    job = job_store.create_job(
        store, Job(user_id="u1", session_id="s1", status=JobStatus.done),
        expires_in=1000,
    )
    with pytest.raises(runner.JobNotResumable):
        runner.resume(store, "u1", job.id, {"approved": True})


def test_resume_orphaned_job_settles_as_timeout():
    """An interrupt job whose runner died (restart) has no registry event.

    It can never be resumed, but it must not keep holding the per-session
    active lock — resume() settles it as `timeout` so the next submit for
    this session can proceed.
    """
    store = InMemoryStore()
    job = job_store.create_job(
        store, Job(user_id="u1", session_id="s1", status=JobStatus.interrupt),
        expires_in=1000,
    )
    settled = runner.resume(store, "u1", job.id, {"approved": True})
    assert settled.status == JobStatus.timeout
    assert job_store.find_active_job(store, "u1", "s1") is None


def test_get_missing_job_raises():
    store = InMemoryStore()
    with pytest.raises(runner.JobNotFound):
        runner.get(store, "u1", "nope")


# ── Router ──────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    store = InMemoryStore()
    graph = _FakeHITLGraph()

    app = FastAPI()
    app.include_router(jobRouter, prefix="/api/v1")
    # The router resolves store/graph via Depends from app.state.app_context —
    # the same shape the lifespan installs in production.
    app.state.app_context = SimpleNamespace(store=store, graph=graph, pool=None)
    app.dependency_overrides[get_current_user_id] = lambda: "user-42"
    with TestClient(app) as c:
        yield c


def test_create_and_poll_job(client):
    resp = client.post("/api/v1/chat/jobs", data={"session_id": "s1", "message": "hi"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    job_id = body["id"]

    for _ in range(100):
        j = client.get(f"/api/v1/chat/jobs/{job_id}").json()
        if j["status"] in ("interrupt", "done"):
            break
        time.sleep(0.05)
    assert j["status"] == "interrupt"


def test_resume_flow_via_router(client):
    job_id = client.post(
        "/api/v1/chat/jobs", data={"session_id": "s1", "message": "hi"}
    ).json()["id"]

    for _ in range(100):
        j = client.get(f"/api/v1/chat/jobs/{job_id}").json()
        if j["status"] == "interrupt":
            break
        time.sleep(0.05)
    assert j["status"] == "interrupt"
    assert j["interrupt"]["type"] == "task_update_approval"

    r = client.post(
        f"/api/v1/chat/jobs/{job_id}/resume",
        json={"decision": {"approved": True}},
    )
    assert r.status_code == 202

    for _ in range(100):
        j = client.get(f"/api/v1/chat/jobs/{job_id}").json()
        if j["status"] == "done":
            break
        time.sleep(0.05)
    assert j["status"] == "done"
    assert j["result"]["reply"] == "final reply"


def test_poll_missing_job_returns_404(client):
    resp = client.get("/api/v1/chat/jobs/nope")
    assert resp.status_code == 404


def test_create_job_conflict_returns_409_with_job_id(client):
    """A 409 on submit carries the blocking job's id so clients can self-heal."""
    first = client.post(
        "/api/v1/chat/jobs", data={"session_id": "s1", "message": "hi"}
    ).json()
    resp = client.post(
        "/api/v1/chat/jobs", data={"session_id": "s1", "message": "second"}
    )
    assert resp.status_code == 409
    assert resp.headers.get("x-active-job-id") == first["id"]
