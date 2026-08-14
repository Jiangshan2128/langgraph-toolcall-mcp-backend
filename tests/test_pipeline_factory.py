"""Tests for the agent-node pipeline factory / lazy singleton.

Covers: factory purity (``create_pipeline``), singleton reuse
(``get_pipeline``), per-node injection (``make_agent_node``), and the
``build_graph(..., pipeline=...)`` wiring.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

from ainote.agents.graph import nodes
from ainote.agents.graph.builder import build_graph


class _FakePipeline:
    """Minimal stand-in for a real Pipeline."""

    def __init__(self) -> None:
        self.runs: list[tuple[Any, Any]] = []

    async def run(self, state, runtime) -> dict:
        self.runs.append((state, runtime))
        return {"messages": [AIMessage(content="ok")]}


def _make_state() -> dict[str, Any]:
    return {"messages": [AIMessage(content="hello")], "user_id": "test-user"}


def _make_runtime() -> MagicMock:
    rt = MagicMock()
    rt.context.user_id = "test-user"
    rt.store = MagicMock()
    return rt


# ======================================================================
# Factory / singleton
# ======================================================================


def test_create_pipeline_returns_fresh_instances():
    """Factory is pure: each call builds an independent pipeline."""
    p1 = nodes.create_pipeline()
    p2 = nodes.create_pipeline()
    assert isinstance(p1, nodes.Pipeline)
    assert p1 is not p2


def test_get_pipeline_reuses_one_instance():
    """Shared pipeline is built lazily and reused across calls."""
    p1 = nodes.get_pipeline()
    p2 = nodes.get_pipeline()
    assert isinstance(p1, nodes.Pipeline)
    assert p1 is p2


# ======================================================================
# make_agent_node
# ======================================================================


async def test_make_agent_node_binds_explicit_pipeline(monkeypatch):
    """Injected pipeline runs, and per-user DingTalk tools are ensured."""
    fake = _FakePipeline()
    ensured = AsyncMock()
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.ensure_user_tools", ensured
    )

    node = nodes.make_agent_node(fake)
    result = await node(_make_state(), _make_runtime())

    assert result == {"messages": [AIMessage(content="ok")]}
    ensured.assert_awaited_once_with("test-user")
    assert len(fake.runs) == 1


async def test_make_agent_node_defaults_to_shared_pipeline(monkeypatch):
    """``None`` resolves to whatever ``get_pipeline()`` returns."""
    fake = _FakePipeline()
    monkeypatch.setattr(nodes, "get_pipeline", lambda: fake)
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.ensure_user_tools", AsyncMock()
    )

    node = nodes.make_agent_node()  # pipeline=None
    await node(_make_state(), _make_runtime())

    assert len(fake.runs) == 1


async def test_agent_node_uses_shared_pipeline(monkeypatch):
    """The standalone ``agent_node`` runs the shared pipeline."""
    fake = _FakePipeline()
    monkeypatch.setattr(nodes, "get_pipeline", lambda: fake)
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.ensure_user_tools", AsyncMock()
    )

    await nodes.agent_node(_make_state(), _make_runtime())

    assert len(fake.runs) == 1


# ======================================================================
# build_graph wiring
# ======================================================================


def test_build_graph_accepts_explicit_pipeline():
    """``build_graph`` accepts a ``pipeline`` kwarg and still compiles."""
    graph = build_graph(
        store=InMemoryStore(),
        checkpointer=MemorySaver(),
        pipeline=_FakePipeline(),
    )
    assert graph is not None
    assert hasattr(graph, "ainvoke")
