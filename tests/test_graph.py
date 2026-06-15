from langgraph.store.memory import InMemoryStore

from app.graph.builder import graph


def test_graph_compiles():
    assert graph is not None
    assert hasattr(graph, "ainvoke")
