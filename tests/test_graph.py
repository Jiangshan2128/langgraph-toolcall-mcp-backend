from langgraph.store.memory import InMemoryStore

from ainote.agents.graph.builder import graph


def test_graph_compiles():
    assert graph is not None
    assert hasattr(graph, "ainvoke")
