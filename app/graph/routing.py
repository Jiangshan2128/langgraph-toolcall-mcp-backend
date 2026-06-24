from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.graph import END

from app.graph.state import AgentState


def route_start(state: AgentState) -> Literal["transcription", "agent"]:
    """Route to transcription subgraph if audio is present, otherwise go directly to agent."""
    if state.get("audio_bytes") is not None and len(state["audio_bytes"]) > 0:
        return "transcription"
    
    return "agent"


def route_after_transcription(state: AgentState) -> Literal["agent"]:
    """After transcription, always route to the main agent."""
    return "agent"


def route_after_agent(
    state: AgentState,
) -> Literal["tools", END]:
    """Route to the tool node if the agent made tool calls, otherwise end."""
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return END