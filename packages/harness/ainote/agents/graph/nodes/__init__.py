"""Node package — LangGraph node definitions for the agent graph.

Contains the two node functions (``agent_node``, ``hitl_node``), the
per-user ``ScopedToolNode``, the conditional-edge routing (``routing``), and
the middleware pipeline that implements ``agent_node``.

Import the node functions from the package, or specific pieces from the
submodules::

    from ainote.agents.graph.nodes import agent_node, hitl_node
    from ainote.agents.graph.nodes.routing import route_after_tools
    from ainote.agents.graph.nodes.scoped_tool_node import ScopedToolNode
"""

from ainote.agents.graph.nodes.agent_node import agent_node
from ainote.agents.graph.nodes.hitl_node import hitl_node

__all__ = [
    "agent_node",
    "hitl_node",
]
