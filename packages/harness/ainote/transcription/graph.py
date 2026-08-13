"""LangGraph subgraph for audio transcription → text.

This subgraph has its own private state (TranscriptionPrivateState),
isolated from the parent graph's AgentState.

State conversion between parent and subgraph is handled in builder.py
at the integration layer.

See: https://docs.langchain.com/oss/python/langgraph/use-subgraphs/
"""

import logging

from langgraph.graph import END, START, StateGraph

from ainote.transcription.service import _transcribe, get_groq_client
from langgraph.graph import MessagesState

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

TRANSCRIPT_TEMPLATE = """ below is the transcript of the audio delimited by double quotes, please reason the transcript carefully about the tasks and then update tasks.
transcript: "{transcript}"
"""

class TranscriptionPrivateState(MessagesState):
    """Private state schema exclusively for the transcription subgraph."""
    audio_bytes: bytes | None
    audio_filename: str | None
    audio_language: str | None
    transcript: str | None


async def transcribe_node(state: TranscriptionPrivateState):
    """Transcribe audio bytes to text using Groq Whisper."""
    audio_bytes = state.get("audio_bytes")
    filename = state.get("audio_filename")
    language = state.get("audio_language")

    if not audio_bytes:
        logger.warning("transcribe_node called with no audio data")
        return {}

    try:
        get_groq_client()
        transcript = await _transcribe(audio_bytes, filename, language, None)
        
        if not transcript.strip():
            logger.warning("Transcription returned empty text")
            transcript = "[Audio content could not be transcribed]"
    except Exception as exc:
        logger.exception("Transcription failed")
        transcript = f"[Transcription failed: {exc}]"

    transcript = TRANSCRIPT_TEMPLATE.format(transcript=transcript)

    return {"messages": HumanMessage(content=transcript)}


def build_transcription_subgraph():
    """Compile the transcription subgraph with private state."""
    g = StateGraph(TranscriptionPrivateState)
    g.add_node("transcribe", transcribe_node)
    g.add_edge(START, "transcribe")
    g.add_edge("transcribe", END)
    return g.compile()


transcription_subgraph = build_transcription_subgraph()