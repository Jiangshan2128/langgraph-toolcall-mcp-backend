import json
from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END

from ainote.agents.graph.state import AgentState


def route_start(state: AgentState) -> Literal["transcription", "agent"]:
    """Route to transcription subgraph if audio is present, otherwise go directly to agent."""
    if state.get("audio_bytes") is not None and len(state["audio_bytes"]) > 0:
        return "transcription"

    return "agent"


def route_after_agent(
    state: AgentState,
) -> Literal["tools", END]:
    """Route to the tool node if the agent made tool calls, otherwise end."""
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return END


def route_after_tools(
    state: AgentState,
) -> Literal["agent", "hitl_node"]:
    """Route to hitl_node if the last tool output contains task proposals, else back to agent."""
    last = state["messages"][-1]
    if isinstance(last, ToolMessage) and last.name == "update_tasks":
        try:
            payload = json.loads(last.content)
            if isinstance(payload, dict) and payload.get("type") == "task_proposals":
                return "hitl_node"
        except (json.JSONDecodeError, TypeError):
            pass
    return "agent"