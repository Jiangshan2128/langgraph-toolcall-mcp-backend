import logging

from langchain_core.messages import HumanMessage

from ainote.agents.memory import delete_all_tasks as _delete_all_tasks
from ainote.agents.memory import delete_task as _delete_task
from ainote.agents.memory import get_tasks
from ainote.agents.memory import update_task as _update_task
from app.chat.thread import resolve_thread_id

logger = logging.getLogger(__name__)


def _notify_thread(graph, user_id: str, session_id: str | None, text: str):
    """Append a system notification to the agent's conversation thread.

    The REST task endpoints mutate the store out-of-band (bypassing LangGraph),
    so the agent's checkpointer thread never sees the change. The next turn the
    model would then 'repair' the conflict (history says task exists, store says
    it doesn't) by re-adding the task. Injecting the mutation as a HumanMessage
    (framed as a system notification) makes history agree with the store and
    removes the conflict. HumanMessage is used over SystemMessage because some
    OpenAI-compatible endpoints only honor the first system message as the true
    system prompt and underweight mid-thread system messages.

    ``session_id`` is the frontend-generated conversation id. It is combined
    with ``user_id`` into the thread_id (same helper as the chat service), so
    the notification lands on the right per-account thread. When ``session_id``
    is omitted (the frontend did not pass one), the notification is skipped —
    we cannot know which conversation to notify.
    """
    if not session_id:
        return
    try:
        config = {"configurable": {"thread_id": resolve_thread_id(user_id, session_id)}}
        graph.update_state(
            config,
            {"messages": [HumanMessage(content=text)]},
        )
    except Exception as exc:
        # Non-fatal: store mutation already succeeded. Don't fail the REST call
        # just because we couldn't notify the thread (e.g. no checkpoint yet).
        logger.warning("Failed to notify thread session=%s: %s", session_id, exc)


def list_tasks(store, user_id: str = "default") -> list[dict]:
    """Return all existing tasks for a user.

    ``store`` is injected by the router (``Depends``) — never a module global.
    """
    return get_tasks(store, user_id)


def delete_task(
    store,
    key: str,
    user_id: str = "default",
    session_id: str | None = None,
    *,
    graph,
) -> list[dict]:
    """Delete a task and return the updated task list."""
    tasks_before = get_tasks(store, user_id)
    task = next((t for t in tasks_before if t["key"] == key), None)
    title = task.get("title", "Unknown") if task else "Unknown"

    _delete_task(store, user_id, key)

    _notify_thread(
        graph,
        user_id,
        session_id,
        f"[SYSTEM NOTIFICATION] Task '{title}' (key={key}) was DELETED via the "
        f"REST API by the user. It no longer exists in the task list. Do NOT "
        f"re-add it by yourself unless the user explicitly requests it.",
    )
    return get_tasks(store, user_id)


def delete_all_tasks(
    store,
    user_id: str = "default",
    session_id: str | None = None,
    *,
    graph,
) -> list[dict]:
    """Delete every task for a user and return the (now empty) task list.

    ``session_id`` 可选：传入时会在对应会话线程注入"全部删除"通知，防止 LLM
    下次基于旧历史误判并重加已删除任务。
    """
    count = _delete_all_tasks(store, user_id)

    _notify_thread(
        graph,
        user_id,
        session_id,
        f"[SYSTEM NOTIFICATION] ALL tasks ({count} total) were DELETED via the "
        f"REST API by the user. The task list is now empty. Do NOT re-add any "
        f"previously mentioned tasks unless the user explicitly requests it.",
    )
    return get_tasks(store, user_id)


def update_task(
    store,
    key: str,
    user_id: str = "default",
    updates: dict = None,
    session_id: str | None = None,
    *,
    graph,
) -> list[dict]:
    """Update a task and return the updated task list."""
    _update_task(store, user_id, key, updates or {})

    after = get_tasks(store, user_id)
    task = next((t for t in after if t["key"] == key), None)
    title = task.get("title", "Unknown") if task else "Unknown"

    _notify_thread(
        graph,
        user_id,
        session_id,
        f"[SYSTEM NOTIFICATION] Task '{title}' (key={key}) was UPDATED via the "
        f"REST API. Fields changed: {updates}. Current task list reflects this.",
    )
    return after
