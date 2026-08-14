"""Tests for the agent-node pipeline factory / lazy singleton.

The pipeline is an internal detail of ``agent_node`` (not exposed through
``build_graph``). These tests cover factory purity (``create_pipeline``),
singleton reuse (``get_pipeline``), and the two injection seams inside
``agent_node`` (``nodes/agent_node.py``): patching ``get_pipeline`` or
setting the module's ``_pipeline`` holder.

The module is fetched via ``importlib.import_module`` rather than
``import ... as`` because ``nodes/__init__.py`` re-exports ``agent_node``
(the function), which would shadow the submodule in a plain ``import``.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage

agent = importlib.import_module("ainote.agents.graph.nodes.agent_node")


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


def _patch_ensure_user_tools(monkeypatch) -> AsyncMock:
    ensured = AsyncMock()
    monkeypatch.setattr(
        "ainote.agents.graph.dingtalk_runtime.ensure_user_tools", ensured
    )
    return ensured


# ======================================================================
# Factory / singleton
# ======================================================================


def test_create_pipeline_returns_fresh_instances():
    """Factory is pure: each call builds an independent pipeline."""
    p1 = agent.create_pipeline()
    p2 = agent.create_pipeline()
    assert isinstance(p1, agent.Pipeline)
    assert p1 is not p2


def test_get_pipeline_reuses_one_instance():
    """Shared pipeline is built lazily and reused across calls."""
    p1 = agent.get_pipeline()
    p2 = agent.get_pipeline()
    assert isinstance(p1, agent.Pipeline)
    assert p1 is p2


# ======================================================================
# Injection seams inside agent_node
# ======================================================================


async def test_agent_node_uses_shared_pipeline(monkeypatch):
    """Patching ``get_pipeline`` swaps what ``agent_node`` runs."""
    fake = _FakePipeline()
    monkeypatch.setattr(agent, "get_pipeline", lambda: fake)
    ensured = _patch_ensure_user_tools(monkeypatch)

    result = await agent.agent_node(_make_state(), _make_runtime())

    assert result == {"messages": [AIMessage(content="ok")]}
    ensured.assert_awaited_once_with("test-user")
    assert len(fake.runs) == 1


async def test_agent_node_uses_injected_holder_pipeline(monkeypatch):
    """Setting ``agent_node._pipeline`` directly is honored and restored safely."""
    fake = _FakePipeline()
    monkeypatch.setattr(agent, "_pipeline", fake)
    _patch_ensure_user_tools(monkeypatch)

    await agent.agent_node(_make_state(), _make_runtime())

    assert len(fake.runs) == 1
