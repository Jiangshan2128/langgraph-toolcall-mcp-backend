from langchain_core.messages import AIMessage
from langgraph.graph import END

from ainote.agents.graph.routing import route_after_agent


def _make_state_with_tool_call(tool_name: str):
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "name": tool_name,
                "args": {},
            }
        ],
    )
    return {"messages": [message]}


def test_route_after_agent_goes_to_tools_when_tool_calls_present():
    state = _make_state_with_tool_call("update_tasks")
    assert route_after_agent(state) == "tools"


def test_route_after_agent_ends_when_no_tool_calls():
    state = {"messages": [AIMessage(content="hello")]}
    assert route_after_agent(state) == END
