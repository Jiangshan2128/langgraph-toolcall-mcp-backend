from langchain_core.messages import AIMessage
from langgraph.graph import END

from ainote.agents.graph.nodes import _build_dingtalk_sync_text
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


def _task(title, priority="P1"):
    return {"title": title, "priority": priority}


def test_dingtalk_sync_text_lists_all_selected_tasks():
    proposed = [
        {"key": "a", "task": _task("跟王总确认供应商合同", "P1")},
        {"key": "b", "task": _task("预订周五飞上海的机票", "P2")},
    ]
    text = _build_dingtalk_sync_text(proposed, {})
    assert "跟王总确认供应商合同" in text
    assert "预订周五飞上海的机票" in text
    assert "P1" in text and "P2" in text


def test_dingtalk_sync_text_only_includes_the_selected_slice():
    # `_build_dingtalk_sync_text` formats whatever slice the caller passes;
    # the per-key selection happens in hitl_node (dingtalk_keys). Pass a
    # subset to confirm nothing outside it leaks into the prompt.
    proposed = [
        {"key": "a", "task": _task("任务A", "P1")},
        {"key": "b", "task": _task("任务B", "P2")},
    ]
    text = _build_dingtalk_sync_text([proposed[1]], {})
    assert "任务B" in text
    assert "任务A" not in text


def test_dingtalk_sync_text_reflects_edited_task():
    proposed = [{"key": "a", "task": _task("旧标题", "P1")}]
    edited = {"a": _task("新标题", "P0")}
    text = _build_dingtalk_sync_text(proposed, edited)
    assert "新标题" in text
    assert "P0" in text
    assert "旧标题" not in text


def test_dingtalk_sync_text_empty_selection_returns_empty():
    assert _build_dingtalk_sync_text([], {}) == ""
