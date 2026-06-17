from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.graph import END

from app.graph.state import AgentState


def route_after_agent(
    state: AgentState,
) -> Literal["tools", END]:
    """Route to the tool node if the agent made tool calls, otherwise end."""
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return END
