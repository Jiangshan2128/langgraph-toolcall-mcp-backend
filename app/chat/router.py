from fastapi import APIRouter, File, Form, UploadFile
from sse_starlette.sse import EventSourceResponse

from app.chat.schemas import ChatResponse, ResumeRequest
from app.chat.service import chat_llm, chat_llm_stream, resume_graph


chatRouter = APIRouter(prefix="/chat", tags=["chat"])


@chatRouter.post("", response_model=ChatResponse)
async def chat(
    message: str = Form(""),
    user_id: str = Form("default"),
    language: str | None = Form(None),
    audio: UploadFile | None = File(None),
):
    """Process text message or audio file (or both).

    Supports both JSON body and multipart form data.

    If audio is provided, it will be transcribed first via the transcription
    subgraph, then the transcript will be processed by the main agent along
    with any text message.

    Returns an ``interrupt`` field when the graph pauses for human approval.
    """
    audio_bytes = None
    audio_filename = None
    if audio:
        audio_bytes = await audio.read()
        audio_filename = audio.filename

    data = await chat_llm(
        message=message,
        user_id=user_id,
        audio_bytes=audio_bytes,
        audio_filename=audio_filename,
        audio_language=language,
    )
    return ChatResponse(
        answer=data["reply"],
        tasks=data["tasks"],
        interrupt=data.get("interrupt"),
    )


@chatRouter.post("/resume", response_model=ChatResponse)
async def chat_resume(request: ResumeRequest):
    """Resume a paused graph with a human decision.

    Call this after the frontend renders an interrupt approval card and the
    user approves, rejects, or edits the proposed task changes.

    The ``decision`` dict is passed directly as the ``resume`` payload to
    the graph via ``Command(resume=decision)``.

    Example decisions:
      - ``{"approved": true}`` — accept all proposed changes
      - ``{"approved": true, "rejected_keys": ["abc-123"]}`` — reject one task
      - ``{"approved": true, "edited_tasks": [{"key": "...", "task": {...}}]}``
      - ``{"approved": false}`` — reject everything
    """
    data = await resume_graph(user_id=request.user_id, decision=request.decision)
    return ChatResponse(
        answer=data["reply"],
        tasks=data["tasks"],
        interrupt=data.get("interrupt"),
    )


@chatRouter.post("/stream")
async def chat_stream(
    message: str = Form(""),
    user_id: str = Form("default"),
    language: str | None = Form(None),
    audio: UploadFile | None = File(None),
):
    """Stream processing result for text message or audio file (or both).

    Supports both JSON body and multipart form data.

    If audio is provided, it will be transcribed first via the transcription
    subgraph, then the transcript will be processed by the main agent along
    with any text message.

    When a human-in-the-loop interrupt fires, an ``interrupt`` SSE event is
    yielded so the frontend can render an approval card.  After the user
    decides, POST to ``/resume`` to continue.
    """
    audio_bytes = None
    audio_filename = None
    if audio:
        audio_bytes = await audio.read()
        audio_filename = audio.filename

    return EventSourceResponse(chat_llm_stream(
        message=message,
        user_id=user_id,
        audio_bytes=audio_bytes,
        audio_filename=audio_filename,
        audio_language=language,
    ))
