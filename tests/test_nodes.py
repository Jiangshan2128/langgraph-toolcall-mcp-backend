from langchain_core.messages import AIMessage
from langgraph.graph import END
from langgraph.store.memory import InMemoryStore

from app.graph.routing import ROUTE_REGISTRY, route_message


def _make_state_with_tool_call(update_type: str):
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "name": "UpdateMemory",
                "args": {"update_type": update_type},
            }
        ],
    )
    return {"messages": [message]}


def test_route_message_registry_matches_worker_nodes():
    assert set(ROUTE_REGISTRY.values()) == {"update_tasks", "update_profile", "update_instructions"}


def test_route_message_dispatches_known_types():
    store = InMemoryStore()
    for update_type, node in ROUTE_REGISTRY.items():
        state = _make_state_with_tool_call(update_type)
        assert route_message(state, store=store) == node


def test_route_message_ends_when_no_tool_calls():
    store = InMemoryStore()
    state = {"messages": [AIMessage(content="hello")]}
    assert route_message(state, store=store) == END


def test_route_message_defaults_to_end_for_unknown_type():
    store = InMemoryStore()
    state = _make_state_with_tool_call("unknown")
    assert route_message(state, store=store) == END
