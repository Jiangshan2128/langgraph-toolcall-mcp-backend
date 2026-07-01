"""Deferred tool-setup cache.

Extracted from ``nodes.py`` so both the agent node and the
``SystemPromptMiddleware`` can import it without circular dependencies.
"""

from app.tools.tool_search import DeferredToolSetup

# Cached deferred-tool setup. Built once after DingTalk MCP tools are loaded,
# then refreshed via refresh_deferred_setup().
_DEFERRED_SETUP: DeferredToolSetup | None = None


def get_deferred_setup_cached() -> DeferredToolSetup | None:
    """Return the cached deferred-tool setup."""
    global _DEFERRED_SETUP
    return _DEFERRED_SETUP


def refresh_deferred_setup(setup: DeferredToolSetup) -> None:
    """Set the cached deferred-tool setup (called from init_graph)."""
    global _DEFERRED_SETUP
    _DEFERRED_SETUP = setup
