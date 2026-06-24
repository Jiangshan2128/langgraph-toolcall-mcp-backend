"""Per-user LangGraph thread_id resolution with time-based rollover.

The checkpointer (MemorySaver) keys conversation history by thread_id. To bound
how much history a single conversation accumulates, each user gets a thread_id
of the form ``{user_id}:{uuid4}`` that is rotated after SESSION_TTL seconds of
inactivity. Both the chat path and the REST task-mutation path resolve the
thread_id through here so task-change notifications land in the same thread the
next chat turn will read.

State is in-process (matches the in-memory checkpointer). Under multiple
uvicorn workers each worker keeps its own map; that is consistent with the
per-process MemorySaver and acceptable for the current deployment.
"""

import logging
import time
import uuid

logger = logging.getLogger(__name__)

# user_id -> (thread_id, last_active_monotonic)
_sessions: dict[str, tuple[str, float]] = {}

SESSION_TTL = 1 * 60  # 空闲超过 5 分钟则滚动生成新 thread_id


def resolve_thread_id(user_id: str = "default") -> str:
    """Return the user's current thread_id, rolling over if idle > SESSION_TTL.

    Each access refreshes the last-active timestamp, so an active conversation
    keeps its thread indefinitely while activity continues; only a 5-minute gap
    starts a fresh thread.
    """
    now = time.monotonic()
    entry = _sessions.get(user_id)
    if entry is None or now - entry[1] > SESSION_TTL:
        thread_id = f"{user_id}:{uuid.uuid4()}"
        _sessions[user_id] = (thread_id, now)
        logger.info("thread rollover user=%s thread_id=%s", user_id, thread_id)
        return thread_id
    # 刷新活跃时间，延长当前 thread 的寿命
    _sessions[user_id] = (entry[0], now)
    return entry[0]
