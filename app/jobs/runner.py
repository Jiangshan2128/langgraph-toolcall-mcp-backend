"""In-process async runner that executes chat jobs against the LangGraph graph.

Each job owns one ``asyncio`` task. The task loops over graph *turns*::

    ainvoke(state) ──► interrupts empty ──► save result (done), exit
                      └─► interrupt fired ─► save status=interrupt + payload,
                                             await a resume event, then
                                             ainvoke(Command(resume=decision))

A resume event + its decision live in an in-memory registry keyed by job id;
``resume()`` fills the decision and sets the event, the runner wakes and
continues from the checkpoint. Because the runner *stays alive across the
interrupt*, multi-round HITL (a second interrupt right after resume) is handled
naturally by the loop.

Orphan handling: on instance restart (CloudBase scale-to-0 / redeploy) the
runner dies with the process, so ``interrupt`` jobs left in the store have no
registry entry — ``resume()`` rejects them with ``JobNotResumable`` and the job
eventually expires via the store's lazy expiry.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from ainote.agents.memory import get_tasks
from ainote.agents.models import Configuration
from app.chat.service import _last_ai_content
from app.chat.thread import resolve_thread_id
from app.common.content_safety import check_text_safety, filter_risky_reply
from app.jobs import store as job_store
from app.jobs.models import Job, JobStatus

logger = logging.getLogger(__name__)

# ── Time budget (seconds) ────────────────────────────────────────────────
# A single graph turn (LLM + tools + HITL interrupt) must finish within this.
RUN_TIMEOUT_SECONDS = 300
# How long the runner waits for a human to approve/reject before giving up.
RESUME_TIMEOUT_SECONDS = 3600
# Absolute job lifetime = worst case (RUN + RESUME); store marks it expired.
JOB_EXPIRY_SECONDS = RUN_TIMEOUT_SECONDS + RESUME_TIMEOUT_SECONDS + 60


# ── Registry: job_id -> asyncio.Event (set when a resume decision arrives) ──
_resume_events: dict[str, asyncio.Event] = {}
# Registry: job_id -> resume decision payload
_resume_decisions: dict[str, dict] = {}
# Set of job ids whose runner task is alive in THIS process. After a process
# restart every persisted "active" job becomes an orphan — nothing is running
# it — so submit() can settle it instead of 409ing the session forever.
_live_jobs: set[str] = set()


class ActiveJobConflict(RuntimeError):
    """A live job already exists for the same (user, session)."""

    def __init__(self, message: str, job_id: str | None = None):
        super().__init__(message)
        self.job_id = job_id


class JobNotFound(RuntimeError):
    """No job with this id exists for the user."""


class JobNotResumable(RuntimeError):
    """Job is not in a resumable state (not paused, or its owner is gone)."""


# ── Public API (called from the router) ─────────────────────────────────


def submit(
    user_id: str = "default",
    session_id: str = "",
    message: str = "",
    audio_bytes: bytes | None = None,
    audio_filename: str | None = None,
    audio_language: str | None = None,
    *,
    store,
    graph,
) -> Job:
    """Create a job and start its background task. Returns the pending job.

    Rejects submission when an active job already exists for the same
    ``(user_id, session_id)`` — two concurrent runs on one LangGraph
    thread_id would race the checkpointer.

    ``store`` / ``graph`` are injected by the router (``Depends``) and captured
    into the background ``_execute`` task.
    """
    # 内容安全:对用户文本做微信 msgSecCheck,命中风险内容直接拒绝
    # (HTTPException → 路由层 400),不进入 LLM 流程。
    if message and not check_text_safety(message):
        logger.warning("rejected risky message user=%s len=%d", user_id, len(message))
        raise HTTPException(
            status_code=400,
            detail="内容包含违规信息，请修改后再发送",
        )

    active = job_store.find_active_job(store, user_id, session_id)
    if active is not None:
        # If the active job still has a live runner in THIS process, it is a
        # genuine in-flight conversation for this session — reject the submit
        # (two concurrent runs would race the LangGraph checkpointer).
        if active.id in _live_jobs:
            raise ActiveJobConflict(
                f"An active job ({active.id}) already exists for this session. "
                f"Poll it or wait for it to finish before sending another message.",
                active.id,
            )
        # Otherwise the job's runner died (process restart / redeploy) while
        # the job was still active. It holds the per-session lock but can
        # never progress — settle it as `timeout` and proceed with the new
        # job, mirroring the orphan handling in resume().
        logger.warning(
            "job %s orphaned (no live runner); settling as timeout", active.id
        )
        active.status = JobStatus.timeout
        active.interrupt = None
        active.error = "Runner died while job was active; job abandoned."
        job_store.save_job(store, active)

    job_store.prune_old_jobs(store, user_id)

    job = Job(
        user_id=user_id,
        session_id=session_id,
        message=message,
        has_audio=audio_bytes is not None,
    )
    job_store.create_job(store, job, expires_in=JOB_EXPIRY_SECONDS)
    _live_jobs.add(job.id)

    asyncio.create_task(
        _execute(
            job_id=job.id,
            user_id=user_id,
            session_id=session_id,
            message=message,
            audio_bytes=audio_bytes,
            audio_filename=audio_filename,
            audio_language=audio_language,
            store=store,
            graph=graph,
        )
    )
    logger.info("job submitted id=%s user=%s session=%s", job.id, user_id, session_id)
    return job


def get(store, user_id: str, job_id: str) -> Job:
    """Read a job for the caller, applying lazy expiry."""
    job = job_store.get_job(store, user_id, job_id)
    if job is None:
        raise JobNotFound(f"Job '{job_id}' not found")
    return job


def resume(store, user_id: str, job_id: str, decision: dict) -> Job:
    """Deliver a human decision to a job paused at HITL and wake its runner.

    Sets the resume decision, flips the job to ``running`` optimistically
    (so the next poll does not re-render the approval card), then sets the
    runner's event. The runner wakes and continues from the checkpoint.
    """
    job = job_store.get_job(store, user_id, job_id)
    if job is None:
        raise JobNotFound(f"Job '{job_id}' not found")

    if job.status != JobStatus.interrupt:
        raise JobNotResumable(
            f"Job '{job_id}' is {job.status.value}; only 'interrupt' jobs can be resumed"
        )

    event = _resume_events.get(job_id)
    if event is None:
        # Orphaned job: its runner died with the server (CloudBase scale-to-0
        # / redeploy). It can never be resumed, but it must not keep holding
        # the per-session active lock — otherwise the next submit for this
        # session 409s forever. Settle it as `timeout` and release the lock.
        logger.warning("job %s orphaned (no runner event); settling as timeout", job_id)
        job.status = JobStatus.timeout
        job.interrupt = None
        job.error = "Runner died while awaiting approval; job abandoned."
        job_store.save_job(store, job)
        return job

    _resume_decisions[job_id] = decision
    job.status = JobStatus.running
    job.interrupt = None
    job_store.save_job(store, job)
    event.set()
    logger.info("job resumed id=%s user=%s", job_id, user_id)
    return job


# ── Background execution ────────────────────────────────────────────────


async def _execute(
    job_id: str,
    user_id: str,
    session_id: str,
    message: str = "",
    audio_bytes: bytes | None = None,
    audio_filename: str | None = None,
    audio_language: str | None = None,
    *,
    store,
    graph,
) -> None:
    job = job_store.get_job(store, user_id, job_id)
    if job is None:
        logger.warning("job %s disappeared before execution", job_id)
        return

    thread_id = resolve_thread_id(user_id, session_id)
    config = {"configurable": {"thread_id": thread_id}}
    context = Configuration(user_id=user_id)

    # First turn: the initial state. Later turns resume with Command.
    command: dict | Command = {
        "messages": [HumanMessage(content=message)] if message else [],
        "user_id": user_id,
    }
    if audio_bytes:
        command["audio_bytes"] = audio_bytes
        command["audio_filename"] = audio_filename
        command["audio_language"] = audio_language

    try:
        await _set_status(store, job, JobStatus.running)

        while True:
            result = await asyncio.wait_for(
                graph.ainvoke(
                    command,
                    config=config,
                    context=context,
                    version="v2",
                ),
                timeout=RUN_TIMEOUT_SECONDS,
            )

            interrupt_data = result.interrupts[0].value if result.interrupts else None
            if interrupt_data is not None:
                resumed = await _pause_for_hitl(store, job, interrupt_data)
                if not resumed:
                    return  # resume timed out → _pause_for_hitl set timeout
                decision = _resume_decisions.pop(job_id, {})
                command = Command(resume=decision)
                continue

            # AI 输出内容安全:命中风险 → 替换为安全占位文案,不外泄原始违规内容。
            # to_thread 避免同步网络调用阻塞事件循环(与其他并发 job 隔离)。
            result = _build_result(result.value, user_id, store=store)
            result["reply"] = await asyncio.to_thread(
                filter_risky_reply, result["reply"]
            )

            await _set_status(store, job, JobStatus.done, result=result)
            logger.info("job done id=%s user=%s", job_id, user_id)
            return

    except asyncio.TimeoutError:
        await _set_status(
            store, job, JobStatus.timeout,
            error=f"Graph turn exceeded {RUN_TIMEOUT_SECONDS}s",
        )
        logger.warning("job %s timed out", job_id)
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        await _set_status(store, job, JobStatus.failed, error=str(exc))
    finally:
        _live_jobs.discard(job_id)
        _resume_events.pop(job_id, None)
        _resume_decisions.pop(job_id, None)


async def _pause_for_hitl(store, job: Job, interrupt_data: dict) -> bool:
    """Record the interrupt and block until ``resume()`` sets our event.

    Returns ``True`` if resumed, ``False`` if the wait timed out (in which
    case the job was already marked ``timeout``).
    """
    # Register the event BEFORE saving status=interrupt, so resume() can never
    # observe "interrupt" without a live event to wake (no race).
    event = asyncio.Event()
    _resume_events[job.id] = event

    await _set_status(store, job, JobStatus.interrupt, interrupt=interrupt_data)
    logger.info(
        "job %s waiting for HITL type=%s", job.id, interrupt_data.get("type")
    )

    try:
        await asyncio.wait_for(event.wait(), timeout=RESUME_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        await _set_status(
            store, job, JobStatus.timeout,
            error=f"No human decision within {RESUME_TIMEOUT_SECONDS}s",
        )
        return False
    return True


async def _set_status(store, job: Job, status: JobStatus, **fields) -> None:
    job.status = status
    for key, value in fields.items():
        setattr(job, key, value)
    job_store.save_job(store, job)


def _build_result(value: dict, user_id: str, *, store) -> dict:
    """Build the final ``{reply, tasks}`` payload, same shape as the sync chat."""
    return {
        "reply": _last_ai_content(value.get("messages", [])),
        "tasks": get_tasks(store, user_id),
    }
