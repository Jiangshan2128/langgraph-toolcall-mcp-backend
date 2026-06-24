from fastapi import APIRouter, File, Form, UploadFile
from sse_starlette.sse import EventSourceResponse

from app.chat.schemas import ChatResponse
from app.chat.service import chat_llm, chat_llm_stream


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
    return ChatResponse(answer=data["reply"], tasks=data["tasks"])


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