"""Polling chat job endpoints.

Every request here is short (< 15s) so it survives CloudBase's
``callContainer`` limit, while the LangGraph agent runs in the background
for as long as it needs:

    POST /chat/jobs              → 202 {job_id, status:"pending"}  (start)
    GET  /chat/jobs/{job_id}     → poll status / result / interrupt
    POST /chat/jobs/{job_id}/resume  → 202 (deliver HITL decision)

Identity comes from ``Authorization: Bearer <Supabase access_token>``; jobs
are namespaced by the resolved ``user_id`` in the store.
"""

from fastapi import APIRouter, HTTPException

from app.common.dependencies import (
    AudioFileDep,
    CurrentUserIdDep,
    GraphDep,
    LanguageFormDep,
    MessageFormDep,
    SessionIdFormDep,
    StoreDep,
    read_audio,
)
from app.jobs import runner
from app.jobs.models import JobResumeRequest

jobRouter = APIRouter(prefix="/chat/jobs", tags=["chat-jobs"])


@jobRouter.post("", status_code=202)
async def create_chat_job(
    session_id: SessionIdFormDep,
    user_id: CurrentUserIdDep,
    store: StoreDep,
    graph: GraphDep,
    message: MessageFormDep = "",
    language: LanguageFormDep = None,
    audio: AudioFileDep = None,
):
    """Submit a chat message for background processing; returns a job to poll.

    Mirrors ``POST /chat`` but returns immediately with a ``job_id``. Poll
    ``GET /chat/jobs/{job_id}`` until ``status`` is ``done``/``failed``/
    ``timeout``. When ``status`` is ``interrupt``, render the approval card
    from the ``interrupt`` payload, then resume via
    ``POST /chat/jobs/{job_id}/resume``.
    """
    audio_bytes, audio_filename = await read_audio(audio)
    try:
        job = runner.submit(
            user_id=user_id,
            session_id=session_id,
            message=message,
            audio_bytes=audio_bytes,
            audio_filename=audio_filename,
            audio_language=language,
            store=store,
            graph=graph,
        )
    except runner.ActiveJobConflict as exc:
        # Expose the blocking job's id so clients can self-heal: reject it
        # (releasing the per-session lock) and retry the submit once.
        headers = {"X-Active-Job-Id": exc.job_id} if exc.job_id else None
        raise HTTPException(status_code=409, detail=str(exc), headers=headers)
    return job.model_dump(mode="json")


@jobRouter.get("/{job_id}")
async def get_chat_job(
    job_id: str, user_id: CurrentUserIdDep, store: StoreDep
):
    """Poll a job's status and payload."""
    try:
        job = runner.get(store, user_id=user_id, job_id=job_id)
    except runner.JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return job.model_dump(mode="json")


@jobRouter.post("/{job_id}/resume", status_code=202)
async def resume_chat_job(
    job_id: str,
    request: JobResumeRequest,
    user_id: CurrentUserIdDep,
    store: StoreDep,
):
    """Resume a job paused at HITL with the user's decision.

    Returns immediately (202) with the job flipped to ``running``; keep
    polling ``GET /chat/jobs/{job_id}`` for the final result.
    """
    try:
        job = runner.resume(store, user_id=user_id, job_id=job_id, decision=request.decision)
    except runner.JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except runner.JobNotResumable as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return job.model_dump(mode="json")
