from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

from ainote.agents.graph.builder import build_graph


def test_graph_compiles():
    graph = build_graph(store=InMemoryStore(), checkpointer=MemorySaver())
    assert graph is not None
    assert hasattr(graph, "ainvoke")
