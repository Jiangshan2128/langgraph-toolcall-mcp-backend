from app.graph.builder import store
from app.store.memory import delete_task as _delete_task
from app.store.memory import get_tasks
from app.store.memory import update_task as _update_task


def delete_task(key: str, user_id: str = "default") -> list[dict]:
    """Delete a task and return the updated task list."""
    _delete_task(store, user_id, key)
    return get_tasks(store, user_id)


def update_task(key: str, user_id: str = "default", updates: dict = None) -> list[dict]:
    """Update a task and return the updated task list."""
    _update_task(store, user_id, key, updates)
    return get_tasks(store, user_id)
