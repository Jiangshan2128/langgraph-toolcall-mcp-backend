from typing import Literal

from langgraph.graph import END

from app.graph.state import AgentState


ROUTE_REGISTRY: dict[str, str] = {
    "task": "update_tasks",
    "profile": "update_profile",
    "instructions": "update_instructions",
}


def route_message(
    state: AgentState,
) -> Literal[END, "update_tasks", "update_profile", "update_instructions"]:
    """Route the last assistant message to a worker node or END."""
    message = state["messages"][-1]
    if not message.tool_calls:
        return END

    tool_call = message.tool_calls[0]
    update_type = tool_call.get("args", {}).get("update_type", "")
    return ROUTE_REGISTRY.get(update_type, END)
