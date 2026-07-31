import logging

from langchain_core.messages import HumanMessage

from ainote.agents.graph.thread import resolve_thread_id
from ainote.agents.graph import builder
from ainote.agents.memory import delete_task as _delete_task
from ainote.agents.memory import get_tasks
from ainote.agents.memory import update_task as _update_task

logger = logging.getLogger(__name__)


def _notify_thread(user_id: str, text: str):
    """Append a system notification to the agent's conversation thread.

    The REST task endpoints mutate the store out-of-band (bypassing LangGraph),
    so the agent's checkpointer thread never sees the change. The next turn the
    model would then 'repair' the conflict (history says task exists, store says
    it doesn't) by re-adding the task. Injecting the mutation as a HumanMessage
    (framed as a system notification) makes history agree with the store and
    removes the conflict. HumanMessage is used over SystemMessage because some
    OpenAI-compatible endpoints only honor the first system message as the true
    system prompt and underweight mid-thread system messages.
    """
    try:
        thread_id = resolve_thread_id(user_id)
        config = {"configurable": {"thread_id": thread_id}}
        builder.graph.update_state(
            config,
            {"messages": [HumanMessage(content=text)]},
        )
    except Exception as exc:
        # Non-fatal: store mutation already succeeded. Don't fail the REST call
        # just because we couldn't notify the thread (e.g. no checkpoint yet).
        logger.warning("Failed to notify thread for user=%s: %s", user_id, exc)


def list_tasks(user_id: str = "default") -> list[dict]:
    """Return all existing tasks for a user."""
    return get_tasks(builder.store, user_id)


def delete_task(key: str, user_id: str = "default") -> list[dict]:
    """Delete a task and return the updated task list."""
    tasks_before = get_tasks(builder.store, user_id)
    task = next((t for t in tasks_before if t["key"] == key), None)
    title = task.get("title", "Unknown") if task else "Unknown"

    _delete_task(builder.store, user_id, key)

    _notify_thread(
        user_id,
        f"[SYSTEM NOTIFICATION] Task '{title}' (key={key}) was DELETED via the "
        f"REST API by the user. It no longer exists in the task list. Do NOT "
        f"re-add it. If asked for the task list, report it as deleted.",
    )
    return get_tasks(builder.store, user_id)


def update_task(key: str, user_id: str = "default", updates: dict = None) -> list[dict]:
    """Update a task and return the updated task list."""
    _update_task(builder.store, user_id, key, updates or {})

    after = get_tasks(builder.store, user_id)
    task = next((t for t in after if t["key"] == key), None)
    title = task.get("title", "Unknown") if task else "Unknown"

    _notify_thread(
        user_id,
        f"[SYSTEM NOTIFICATION] Task '{title}' (key={key}) was UPDATED via the "
        f"REST API. Fields changed: {updates}. Current task list reflects this.",
    )
    return after
