"""Tests for thread-id resolution (per-user conversation isolation).

The LangGraph ``thread_id`` is the checkpoint key holding conversation
history. It must bind both the user and the session, so switching accounts
with the same ``session_id`` never continues the previous user's thread.
"""

from app.chat.thread import resolve_thread_id


def test_same_session_different_user_yields_different_thread():
    """Switching accounts with the same session_id must NOT share history."""
    a = resolve_thread_id("user-a", "session-1")
    b = resolve_thread_id("user-b", "session-1")
    assert a != b


def test_same_user_different_session_yields_different_thread():
    """New session_id starts a fresh conversation (per-user)."""
    a = resolve_thread_id("user-a", "session-1")
    b = resolve_thread_id("user-a", "session-2")
    assert a != b


def test_same_user_same_session_is_deterministic():
    """Resume must reach the exact same thread as the original call."""
    assert resolve_thread_id("user-a", "session-1") == resolve_thread_id("user-a", "session-1")


def test_anonymous_default_is_also_namespaced():
    """Even the 'default' user's threads are session-scoped, not global."""
    a = resolve_thread_id("default", "session-1")
    b = resolve_thread_id("default", "session-2")
    assert a != b
    assert "default" in a
