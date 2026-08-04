"""Thread-id resolution: conversation threads must be isolated per user.

The LangGraph ``thread_id`` is the checkpoint key that holds conversation
history. It must bind BOTH the caller's user id and the frontend session id —
otherwise switching accounts with the same ``session_id`` would continue the
previous user's conversation, and two users sharing a ``session_id`` would
collide on the same thread.

``session_id`` stays a frontend-generated random value; ``user_id`` is
resolved from the Supabase token. Concatenating them namespaces the thread
per user while preserving the frontend's ability to start a fresh
conversation by sending a new ``session_id``.
"""


def resolve_thread_id(user_id: str, session_id: str) -> str:
    """Return the LangGraph ``thread_id`` for a (user, session) pair.

    ``user_id`` is the authenticated user (or ``"default"``); ``session_id``
    is the frontend-generated conversation id. The two are joined so history
    never leaks across accounts.
    """
    return f"{user_id}:{session_id}"
