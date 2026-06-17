from typing import Annotated, Literal

from langchain.tools import tool
from langgraph.prebuilt.tool_node import InjectedState, InjectedStore
from langgraph.store.base import BaseStore

from app.graph.state import AgentState
from app.store.memory import delete_task as _delete_task
from app.store.memory import get_tasks
from app.store.memory import update_task as _update_task


def _user_id(state: AgentState) -> str:
    return state.get("user_id") or "default"


def _find_task_by_title(store: BaseStore, user_id: str, title: str) -> dict | None:
    """Find a task whose title contains the given substring (case-insensitive)."""
    lower = title.lower()
    for task in get_tasks(store, user_id):
        if lower in task.get("title", "").lower():
            return task
    return None


@tool
def mark_task_done(
    title: str,
    state: Annotated[AgentState, InjectedState()],
    store: Annotated[BaseStore, InjectedStore()],
) -> str:
    """Mark a task as done by matching its title.

    Use when the user says a task is completed, finished, or done.
    """
    user_id = _user_id(state)
    task = _find_task_by_title(store, user_id, title)
    if not task:
        return f"No task matching '{title}' was found."

    _update_task(store, user_id, task["key"], {"status": "done"})
    return f"Task '{task['title']}' marked as done."


@tool
def update_task_priority(
    title: str,
    priority: Literal["P0", "P1", "P2"],
    state: Annotated[AgentState, InjectedState()],
    store: Annotated[BaseStore, InjectedStore()],
) -> str:
    """Update a task's priority by matching its title.

    P0 = urgent today, P1 = important, P2 = routine.
    """
    user_id = _user_id(state)
    task = _find_task_by_title(store, user_id, title)
    if not task:
        return f"No task matching '{title}' was found."

    _update_task(store, user_id, task["key"], {"priority": priority})
    return f"Task '{task['title']}' priority updated to {priority}."


@tool
def delete_task_by_title(
    title: str,
    state: Annotated[AgentState, InjectedState()],
    store: Annotated[BaseStore, InjectedStore()],
) -> str:
    """Delete a task by matching its title.

    Use when the user explicitly asks to remove or delete a task.
    """
    user_id = _user_id(state)
    task = _find_task_by_title(store, user_id, title)
    if not task:
        return f"No task matching '{title}' was found."

    _delete_task(store, user_id, task["key"])
    return f"Task '{task['title']}' deleted."
