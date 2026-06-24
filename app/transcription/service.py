"""Transcription service — Groq Whisper (audio → text).

Provides low-level transcription primitives used by the transcription subgraph
(app.transcription.graph). This module does NOT depend on graph.py, avoiding
circular imports.
"""

import logging
from io import BytesIO

from groq import APIStatusError, AsyncGroq

from app.core.config import settings

logger = logging.getLogger(__name__)

# Groq free tier caps a single transcription request at 25 MB.
GROQ_MAX_BYTES = 25 * 1024 * 1024
# Hard ceiling to protect the server from pathological uploads.
MAX_INPUT_BYTES = 100 * 1024 * 1024  # 100 MB

# Lazily-built async Groq client. AsyncGroq keeps the event loop unblocked in
# the FastAPI handler. Reused across requests (shared underlying httpx client).
_groq_client: AsyncGroq | None = None


def get_groq_client() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(
            api_key=settings.GROQ_API_KEY,
            base_url=settings.GROQ_BASE_URL,
        )
    return _groq_client


# async def transcribe_and_summarize(
#     audio_bytes: bytes,
#     filename: str | None = None,
#     language: str | None = None,
#     model: str | None = None,
#     style: str = "default",
#     user_id: str = "default",
# ) -> tuple:
#     """Transcribe audio to text and extract tasks via LangGraph subgraph.

#     Returns:
#         Tuple of (transcript, summary, tasks)

#     Raises:
#         ValueError: If transcription fails
#     """
#     if not settings.GROQ_API_KEY:
#         raise ValueError("GROQ_API_KEY is not configured on the server.")

#     if len(audio_bytes) > MAX_INPUT_BYTES:
#         raise ValueError(f"Audio too large: {len(audio_bytes)} bytes (max {MAX_INPUT_BYTES}).")

#     # 1) TRANSCRIBE via Groq Whisper (chunk if > 25 MB)
#     try:
#         transcript = await _transcribe(audio_bytes, filename, language, model)
#     except APIStatusError as exc:
#         logger.exception("Groq transcription HTTP error: %s", exc.body)
#         raise ValueError(f"Transcription failed: {exc.status_code}") from exc
#     except Exception as exc:
#         logger.exception("Groq transcription failed")
#         raise ValueError(f"Transcription failed: {exc}") from exc

#     if not transcript.strip():
#         raise ValueError("Transcription returned empty text.")

#     # 2) Run the transcription SUBGRAPH (agent → update_tasks → summary)
#     thread_id = f"transcribe:{user_id}:{uuid.uuid4()}"
#     config = {"configurable": {"thread_id": thread_id}}
#     try:
#         result = await transcription_graph.ainvoke(
#             {"messages": [HumanMessage(content=_wrap_transcript(transcript, style))],
#              "user_id": user_id},
#             config=config,
#             context=Configuration(user_id=user_id),
#         )
#         # Extract the final summary from the messages
#         summary = ""
#         if "messages" in result:
#             messages = result["messages"]
#             if messages:
#                 last_msg = messages[-1]
#                 if isinstance(last_msg, AIMessage) and last_msg.content:
#                     summary = last_msg.content
#     except Exception as exc:
#         logger.exception("summary graph error user=%s", user_id)
#         raise ValueError(f"Summary failed: {exc}") from exc

#     # 3) Get the task list (tasks extracted by update_tasks)
#     try:
#         tasks = get_tasks(builder.store, user_id)
#     except Exception as exc:
#         logger.exception("get_tasks error user=%s", user_id)
#         tasks = []

#     return transcript, summary, tasks


# def _wrap_transcript(transcript: str, style: str) -> str:
#     """Frame the raw transcript with the requested summary style."""
#     style_hint = {
#         "bullets": "Format the summary as concise bullet points.",
#         "meeting": "This is a meeting recording. Highlight decisions, owners, and action items.",
#         "concise": "Keep the summary to about 3 sentences.",
#     }.get(style, "Provide a well-structured summary.")

#     return (
#         f"Below is the transcript of an audio recording. {style_hint}\n\n"
#         f"--- TRANSCRIPT START ---\n{transcript}\n--- TRANSCRIPT END ---"
#     )


async def _transcribe(audio_bytes: bytes, filename: str | None, language: str | None, model: str | None) -> str:
    """Transcribe audio via the Groq SDK, chunking if the file exceeds 25 MB."""
    model = model or settings.GROQ_TRANSCRIPTION_MODEL

    if len(audio_bytes) <= GROQ_MAX_BYTES:
        return await _call_groq(audio_bytes, filename, language, model)

    return await _transcribe_chunked(audio_bytes, filename, language, model)


async def _call_groq(
    audio_bytes: bytes,
    filename: str | None,
    language: str | None,
    model: str,
) -> str:
    """Single Groq transcription call via the official SDK.

    Uses verbose_json so we can fall back to per-segment text if the top-level
    `text` field is empty. temperature=0 for deterministic transcription.
    """
    file_tuple = (filename or "audio.wav", BytesIO(audio_bytes), "application/octet-stream")
    kwargs = {
        "model": model,
        "file": file_tuple,
        "response_format": "verbose_json",
        "temperature": 0.0,
    }
    if language:
        kwargs["language"] = language

    client = get_groq_client()
    result = await client.audio.transcriptions.create(**kwargs)

    # The SDK returns a typed object exposing .text and .segments (when verbose).
    text = getattr(result, "text", None) or ""
    if not text:
        segments = getattr(result, "segments", None) or []
        text = " ".join(getattr(seg, "text", "") or "" for seg in segments)
    print(f"\n\n{text}\n\n")
    return text.strip()


async def _transcribe_chunked(
    audio_bytes: bytes,
    filename: str | None,
    language: str | None,
    model: str,
) -> str:
    """Split oversized audio into overlapping < 25 MB parts, transcribe each, join.

    Groq recommends breaking long audio into *overlapping* segments and merging
    the results (see console.groq.com/docs/speech-to-text). We split via ffmpeg
    into 16 kHz mono Opus segments sized under the cap with a small overlap so
    words at segment edges are captured by at least one chunk; the LLM
    summarization step absorbs minor edge duplication.

    Requires `ffmpeg` on PATH. The Android client pre-compresses to 16 kHz mono
    Opus, so this path is only hit for very long (multi-hour) recordings.
    """
    import asyncio
    import tempfile
    from pathlib import Path

    from app.transcription._ffmpeg import split_audio_into_overlapping_chunks

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src_name = filename or "audio"
        # Preserve an extension so ffmpeg can detect the container; default to .m4a.
        suffix = Path(src_name).suffix or ".m4a"
        src_file = tmp_path / f"input{suffix}"
        src_file.write_bytes(audio_bytes)

        try:
            chunk_files = await asyncio.to_thread(
                split_audio_into_overlapping_chunks, src_file, tmp_path, max_bytes=GROQ_MAX_BYTES
            )
        except FileNotFoundError:
            raise RuntimeError(
                "ffmpeg is not installed on the server; cannot chunk oversized audio."
            )

        if not chunk_files:
            raise RuntimeError("Audio chunking produced no segments.")

        parts: list[str] = []
        for chunk_file in chunk_files:
            chunk_bytes = chunk_file.read_bytes()
            text = await _call_groq(chunk_bytes, chunk_file.name, language, model)
            if text:
                parts.append(text)

    return "\n".join(parts).strip()