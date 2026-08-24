"""Unit tests for LangGraph-level fault tolerance (retry_on / error handler / wiring)."""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import NodeError
from langgraph.store.memory import InMemoryStore

from ainote.agents.graph.builder import build_graph
from ainote.agents.graph.fault_tolerance import (
    RETRY_POLICY,
    graph_error_handler,
    retry_on,
)


# Fake OpenAI/Groq-style status error. retry_on matches on the MRO class NAME
# ("APIStatusError"), so the fake MUST carry the exact SDK name to reproduce
# the hierarchy without importing openai/groq.
class APIStatusError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class BadRequestError(Exception):
    """Mimics langchain-core's BadRequestError (not an APIStatusError)."""


class TestRetryOn:
    def test_timeout_is_retryable(self):
        # TimeoutError / asyncio.TimeoutError are OSError subclasses, which
        # default_retry_on excludes — retry_on must still absorb them.
        assert retry_on(TimeoutError("timed out")) is True
        assert retry_on(ConnectionError("connection refused")) is True

    def test_sdk_5xx_is_retryable(self):
        assert retry_on(APIStatusError(500)) is True
        assert retry_on(APIStatusError(503)) is True

    def test_sdk_rate_limit_is_retryable(self):
        assert retry_on(APIStatusError(429)) is True

    def test_sdk_4xx_is_fatal(self):
        # Bad request / auth / not-found will never succeed on retry.
        assert retry_on(APIStatusError(400)) is False
        assert retry_on(APIStatusError(401)) is False
        assert retry_on(APIStatusError(404)) is False

    def test_bad_request_error_is_fatal(self):
        assert retry_on(BadRequestError()) is False

    def test_logic_errors_are_fatal(self):
        assert retry_on(RuntimeError("boom")) is False
        assert retry_on(ValueError("bad value")) is False

    def test_policy_uses_our_predicate(self):
        assert RETRY_POLICY.max_attempts == 3
        assert RETRY_POLICY.retry_on is retry_on


class TestGraphErrorHandler:
    def test_returns_friendly_message(self):
        error = NodeError(node="agent", error=RuntimeError("boom"))
        result = graph_error_handler({"messages": []}, error)
        messages = result["messages"]
        assert len(messages) == 1
        assert isinstance(messages[0], AIMessage)
        assert "抱歉" in messages[0].content
        # Generic failure gets the generic apology, not the provider-specific one.
        assert "模型服务" not in messages[0].content

    def test_bad_request_error_gets_provider_apology(self):
        error = NodeError(node="agent", error=BadRequestError())
        result = graph_error_handler({"messages": []}, error)
        assert "模型服务暂时不可用" in result["messages"][0].content

    def test_400_status_error_gets_provider_apology(self):
        # OpenAI/Groq-style APIStatusError(400) — fatal, provider-specific text.
        error = NodeError(node="agent", error=APIStatusError(400))
        result = graph_error_handler({"messages": []}, error)
        assert "模型服务暂时不可用" in result["messages"][0].content

    def test_does_not_leak_exception_detail(self):
        # Connection strings / key fragments must never reach the client.
        error = NodeError(node="agent", error=RuntimeError("postgres://secret-dsn"))
        result = graph_error_handler({"messages": []}, error)
        assert "secret-dsn" not in result["messages"][0].content


class TestGraphWiring:
    def test_graph_compiles_with_fault_tolerance(self):
        graph = build_graph(store=InMemoryStore(), checkpointer=MemorySaver())
        assert graph is not None

        # set_node_defaults installed a shared compensation node...
        assert "__default_error_handler__" in graph.builder.nodes

        # ...and every node got the retry policy; timeouts are per-node.
        for name in ("agent", "tools", "transcription", "hitl_node"):
            assert graph.builder.nodes[name].retry_policy is not None, name
        assert graph.builder.nodes["agent"].timeout is not None
        assert graph.builder.nodes["tools"].timeout is not None
        assert graph.builder.nodes["transcription"].timeout is not None
        # hitl_node relies on interrupt(), which bypasses retry/timeout.
        assert graph.builder.nodes["hitl_node"].timeout is None
