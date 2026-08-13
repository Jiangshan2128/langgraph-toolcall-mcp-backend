"""Durable job persistence on top of the LangGraph store.

Jobs live in the ``("job", user_id)`` namespace of ``builder.store`` — the
same Postgres/InMemory store used for tasks and profiles. Jobs are therefore
per-user isolated and survive instance restarts: a poll can read a job even
after the process that created it has been replaced by CloudBase scale-to-0.

Stale-run protection: a job in an active status past its ``expires_at`` is
presumed orphaned (its runner died with the instance) and is lazily marked
``timeout`` the next time it is read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from langgraph.store.base import BaseStore

from app.jobs.models import ACTIVE_STATUSES, TERMINAL_STATUSES, Job, JobStatus

NAMESPACE_PREFIX = "job"

_store_provider = None


def _get_store() -> BaseStore:
    """Return the shared store, importing ``builder`` lazily.

    ``builder`` is imported at first use (not module import) so the job store
    can be unit-tested with an injected ``InMemoryStore`` without booting the
    whole graph.
    """
    if _store_provider is not None:
        return _store_provider()
    from ainote.agents.graph import builder

    return builder.store


def set_store_provider(provider) -> None:
    """Test hook: override how the store is resolved (e.g. a fixed InMemoryStore)."""
    global _store_provider
    _store_provider = provider


def _namespace(user_id: str) -> tuple[str, str]:
    return (NAMESPACE_PREFIX, user_id)


def _is_expired(job: Job) -> bool:
    try:
        return datetime.now(timezone.utc) > datetime.fromisoformat(job.expires_at)
    except ValueError:
        return True


def create_job(store: BaseStore, job: Job, expires_in: int) -> Job:
    """Persist a new job with an absolute ``expires_at`` = now + ``expires_in``."""
    job.expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).isoformat()
    job.updated_at = datetime.now(timezone.utc).isoformat()
    store.put(_namespace(job.user_id), job.id, job.model_dump(mode="json"))
    return job


def get_job(store: BaseStore, user_id: str, job_id: str) -> Job | None:
    """Read a job, lazily expiring stale active jobs before returning."""
    item = store.get(_namespace(user_id), job_id)
    if item is None:
        return None
    job = Job.model_validate(item.value)
    if job.status in ACTIVE_STATUSES and _is_expired(job):
        job.status = JobStatus.timeout
        job.updated_at = datetime.now(timezone.utc).isoformat()
        store.put(_namespace(user_id), job.id, job.model_dump(mode="json"))
    return job


def save_job(store: BaseStore, job: Job) -> Job:
    """Persist a job, refreshing ``updated_at``."""
    job.updated_at = datetime.now(timezone.utc).isoformat()
    store.put(_namespace(job.user_id), job.id, job.model_dump(mode="json"))
    return job


def find_active_job(store: BaseStore, user_id: str, session_id: str) -> Job | None:
    """Return an active job for the same (user, session), or ``None``.

    Guards against two concurrent runs sharing one LangGraph thread_id
    (the checkpointer serializes by thread_id and would otherwise race).

    An active-status job that has passed ``expires_at`` is presumed orphaned
    (its runner died with the instance) — it does NOT count as active, so the
    per-session lock is released and the next submit can proceed.
    """
    for item in store.search(_namespace(user_id)):
        job = Job.model_validate(item.value)
        if job.status in ACTIVE_STATUSES and _is_expired(job):
            # Lazily settle the stale job to timeout so it can't block a submit.
            job.status = JobStatus.timeout
            job.updated_at = datetime.now(timezone.utc).isoformat()
            store.put(_namespace(user_id), job.id, job.model_dump(mode="json"))
            continue
        if job.session_id == session_id and job.status in ACTIVE_STATUSES:
            return job
    return None


def prune_old_jobs(store: BaseStore, user_id: str, max_age_hours: float = 24) -> int:
    """Delete terminal jobs older than ``max_age_hours``; return count removed.

    Keeps the per-user ``job`` namespace bounded. Called opportunistically on
    submit — no background sweeper needed.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    removed = 0
    namespace = _namespace(user_id)
    for item in store.search(namespace):
        job = Job.model_validate(item.value)
        try:
            created_at = datetime.fromisoformat(job.created_at)
        except ValueError:
            continue
        if job.status in TERMINAL_STATUSES and created_at < cutoff:
            store.delete(namespace, job.id)
            removed += 1
    return removed
