from langgraph.store.memory import InMemoryStore

from app.graph.state import AgentState
from app.store.memory import put_tasks
from app.tools.core.tasks import (
    delete_task_by_title,
    mark_task_done,
    update_task_priority,
)


def _state(user_id: str = "default") -> AgentState:
    return {"messages": [], "user_id": user_id}


def test_mark_task_done():
    store = InMemoryStore()
    state = _state()
    put_tasks(store, "default", [("key-1", {"title": "需求分析", "status": "not started"})])

    result = mark_task_done.func(title="需求分析", state=state, store=store)

    tasks = store.search(("task", "default"))
    assert tasks[0].value["status"] == "done"
    assert "需求分析" in result


def test_update_task_priority():
    store = InMemoryStore()
    state = _state()
    put_tasks(store, "default", [("key-1", {"title": "UI设计", "priority": "P2"})])

    result = update_task_priority.func(title="UI设计", priority="P0", state=state, store=store)

    tasks = store.search(("task", "default"))
    assert tasks[0].value["priority"] == "P0"
    assert "P0" in result


def test_delete_task_by_title():
    store = InMemoryStore()
    state = _state()
    put_tasks(store, "default", [("key-1", {"title": "测试用例"})])

    result = delete_task_by_title.func(title="测试用例", state=state, store=store)

    tasks = store.search(("task", "default"))
    assert len(tasks) == 0
    assert "deleted" in result.lower() or "删除" in result


def test_task_tool_not_found():
    store = InMemoryStore()
    state = _state()

    result = mark_task_done.func(title="不存在的任务", state=state, store=store)

    assert "No task matching" in result
