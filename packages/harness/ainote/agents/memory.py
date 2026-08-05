from langgraph.store.base import BaseStore


PROFILE_NS = "profile"
TASK_NS = "task"
INSTRUCTIONS_NS = "instructions"


def _namespace(prefix: str, user_id: str):
    return (prefix, user_id)


def get_profile(store: BaseStore, user_id: str):
    """Return the latest profile memory for a user, or None."""
    items = store.search(_namespace(PROFILE_NS, user_id))
    return items[0].value if items else None


def put_profile(store: BaseStore, user_id: str, data: dict, key: str | None = None):
    """Persist a profile memory."""
    store.put(
        _namespace(PROFILE_NS, user_id),
        key or "user_profile",
        data,
    )


def get_tasks(store: BaseStore, user_id: str) -> list[dict]:
    """Return all task memories for a user, with store key injected."""
    items = store.search(_namespace(TASK_NS, user_id))
    return [{"key": item.key, **item.value} for item in items]


def put_tasks(store: BaseStore, user_id: str, items: list[tuple[str, dict]]):
    """Persist many task memories as (key, value) pairs."""
    namespace = _namespace(TASK_NS, user_id)
    for key, value in items:
        store.put(namespace, key, value)


def delete_task(store: BaseStore, user_id: str, key: str):
    """Delete a single task by key."""
    store.delete(_namespace(TASK_NS, user_id), key)


def delete_all_tasks(store: BaseStore, user_id: str) -> int:
    """Delete every task memory for a user; return the number deleted."""
    namespace = _namespace(TASK_NS, user_id)
    keys = [item.key for item in store.search(namespace)]
    for key in keys:
        store.delete(namespace, key)
    return len(keys)


def update_task(store: BaseStore, user_id: str, key: str, updates: dict):
    """Update a task by key, merging updates into the existing value."""
    namespace = _namespace(TASK_NS, user_id)
    item = store.get(namespace, key)
    if item is None:
        raise KeyError(f"Task with key '{key}' not found")
    merged = {**item.value, **updates}
    store.put(namespace, key, merged)
    return merged


def get_instructions(store: BaseStore, user_id: str) -> dict | None:
    """Return stored instructions memory for a user, or None."""
    item = store.get(_namespace(INSTRUCTIONS_NS, user_id), "user_instructions")
    return item.value if item else None


def put_instructions(store: BaseStore, user_id: str, data: dict):
    """Persist instructions memory for a user."""
    store.put(
        _namespace(INSTRUCTIONS_NS, user_id),
        "user_instructions",
        data,
    )
