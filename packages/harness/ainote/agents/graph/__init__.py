"""Graph module for agent execution.

This module provides the LangGraph-based agent execution pipeline,
including state management, node functions, and graph building.

Import directly from submodules to avoid circular dependencies::

    from ainote.agents.graph.builder import build_graph
    from ainote.agents.graph.state import AgentState
    from ainote.agents.graph.nodes import agent_node, hitl_node

``builder`` no longer exposes a module-level ``graph`` singleton — the graph is
built by ``create_app_context`` at startup and injected via ``Depends``.
"""


def __getattr__(name: str):
    """Lazy re-exports — avoids importing builder (which imports ALL_TOOLS)
    during module initialization, preventing a circular import with app.tools.
    """
    if name == "build_graph":
        from ainote.agents.graph.builder import build_graph

        return build_graph
    if name == "agent_node":
        from ainote.agents.graph.nodes import agent_node

        return agent_node
    if name == "hitl_node":
        from ainote.agents.graph.nodes import hitl_node

        return hitl_node
    if name == "AgentState":
        from ainote.agents.graph.state import AgentState

        return AgentState
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "agent_node",
    "AgentState",
    "build_graph",
    "hitl_node",
]
