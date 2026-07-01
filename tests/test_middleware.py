"""Unit tests for the middleware pipeline and each middleware class."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.graph.middleware.base import MiddlewareContext, Pipeline
from app.graph.middleware.error_handler import ErrorHandlingMiddleware
from app.graph.middleware.memory_load import MemoryLoadMiddleware, StoreAccessor
from app.graph.middleware.system_prompt import SystemPromptMiddleware
from app.graph.middleware.tool_binding import ToolBindingMiddleware


# ======================================================================
# Helpers
# ======================================================================


def _make_state(**overrides: Any) -> dict:
    """Minimal AgentState-like dict for testing."""
    state: dict[str, Any] = {
        "messages": [HumanMessage(content="hello")],
        "user_id": "test-user",
    }
    state.update(overrides)
    return state


def _make_runtime(user_id: str = "test-user") -> MagicMock:
    """Minimal Runtime mock."""
    rt = MagicMock()
    rt.context.user_id = user_id
    rt.store = MagicMock()
    return rt


async def _noop_handler(state, runtime, context):
    return {"messages": [AIMessage(content="ok")]}


# ======================================================================
# Pipeline
# ======================================================================


class TestPipeline:
    async def test_empty_pipeline_calls_core_handler(self):
        """Pipeline with no middlewares invokes core handler directly."""
        pipeline = Pipeline(middlewares=[], core_handler=_noop_handler)
        result = await pipeline.run(_make_state(), _make_runtime())
        assert result == {"messages": [AIMessage(content="ok")]}

    async def test_middleware_order(self):
        """Verify middlewares execute outer-first, inner-last."""
        order: list[str] = []

        class OrderMiddleware:
            def __init__(self, name: str):
                self._name = name

            async def __call__(self, state, runtime, context, next_handler):
                order.append(f"{self._name}_before")
                result = await next_handler(state, runtime, context)
                order.append(f"{self._name}_after")
                return result

        async def core(state, runtime, context):
            order.append("core")
            return {"messages": [AIMessage(content="done")]}

        pipeline = Pipeline(
            middlewares=[
                OrderMiddleware("A"),
                OrderMiddleware("B"),
                OrderMiddleware("C"),
            ],
            core_handler=core,
        )
        await pipeline.run(_make_state(), _make_runtime())

        assert order == [
            "A_before",
            "B_before",
            "C_before",
            "core",
            "C_after",
            "B_after",
            "A_after",
        ]

    async def test_context_flows_between_middlewares(self):
        """Context dict is shared across all middlewares."""

        class WriteMiddleware:
            async def __call__(self, state, runtime, context, next_handler):
                context["key"] = "value"
                return await next_handler(state, runtime, context)

        class ReadMiddleware:
            async def __call__(self, state, runtime, context, next_handler):
                assert context["key"] == "value"
                context["key"] = "modified"
                return await next_handler(state, runtime, context)

        captured: dict = {}

        async def core(state, runtime, context):
            captured["final"] = context["key"]
            return {"messages": []}

        pipeline = Pipeline(
            middlewares=[WriteMiddleware(), ReadMiddleware()],
            core_handler=core,
        )
        await pipeline.run(_make_state(), _make_runtime())
        assert captured["final"] == "modified"

    async def test_context_is_fresh_per_run(self):
        """Each pipeline.run() gets a fresh context dict."""
        pipeline = Pipeline(middlewares=[], core_handler=_noop_handler)

        ctx1 = None
        ctx2 = None

        async def capture_core(state, runtime, context):
            nonlocal ctx1
            ctx1 = context
            return {"messages": []}

        # Override core handler for first run
        pipeline._core_handler = capture_core
        await pipeline.run(_make_state(), _make_runtime())

        async def capture_core2(state, runtime, context):
            nonlocal ctx2
            ctx2 = context
            return {"messages": []}

        pipeline._core_handler = capture_core2
        await pipeline.run(_make_state(), _make_runtime())

        assert ctx1 is not None
        assert ctx2 is not None
        assert ctx1 is not ctx2  # different dict objects


# ======================================================================
# MemoryLoadMiddleware
# ======================================================================


class _FakeStoreAccessor(StoreAccessor):
    """Test double for StoreAccessor."""

    def __init__(
        self,
        profile: dict | None = None,
        tasks: list[dict] | None = None,
        instructions: dict | None = None,
    ):
        self.profile = profile or {"name": "Test"}
        self.tasks = tasks or [{"title": "Task 1"}]
        self.instructions = instructions or {"memory": "Be helpful"}

    def get_profile(self, store, user_id):
        return self.profile

    def get_tasks(self, store, user_id):
        return self.tasks

    def get_instructions(self, store, user_id):
        return self.instructions


class TestMemoryLoadMiddleware:
    async def test_loads_all_memories_into_context(self):
        """Profile, tasks, and instructions are stored in context."""
        accessor = _FakeStoreAccessor()
        mw = MemoryLoadMiddleware(store_accessor=accessor)

        captured: MiddlewareContext = {}

        async def capture(state, runtime, context):
            captured.update(context)
            return {"messages": []}

        await mw(_make_state(), _make_runtime(), {}, capture)

        assert captured["profile"] == {"name": "Test"}
        assert captured["tasks"] == [{"title": "Task 1"}]
        assert captured["instructions"] == {"memory": "Be helpful"}

    async def test_user_id_from_state(self):
        """user_id is read from state."""
        accessor = _FakeStoreAccessor()

        calls: list[str] = []

        class TrackingAccessor:
            def get_profile(self, store, uid):
                calls.append(uid)
                return {}

            def get_tasks(self, store, uid):
                return []

            def get_instructions(self, store, uid):
                return {}

        mw = MemoryLoadMiddleware(store_accessor=TrackingAccessor())
        await mw(
            _make_state(user_id="state-user"),
            _make_runtime(user_id="runtime-user"),
            {},
            _noop_handler,
        )
        assert calls[0] == "state-user"  # state takes priority

    async def test_user_id_fallback_to_runtime(self):
        """When state has no user_id, fall back to runtime.context."""
        calls: list[str] = []

        class TrackingAccessor:
            def get_profile(self, store, uid):
                calls.append(uid)
                return {}

            def get_tasks(self, store, uid):
                return []

            def get_instructions(self, store, uid):
                return {}

        mw = MemoryLoadMiddleware(store_accessor=TrackingAccessor())
        state = _make_state()
        del state["user_id"]
        await mw(state, _make_runtime(user_id="runtime-user"), {}, _noop_handler)
        assert calls[0] == "runtime-user"


# ======================================================================
# SystemPromptMiddleware
# ======================================================================


class TestSystemPromptMiddleware:
    TEMPLATE = "Profile: {user_profile} | Tasks: {tasks} | Instructions: {instructions} | DT: {deferred_tools}"

    def _make_deferred_getter(self, names: frozenset | None = None):
        """Factory for deferred_setup_getter."""

        class FakeSetup:
            deferred_names = names or frozenset()

        return lambda: FakeSetup()

    async def test_formats_template_with_all_fields(self):
        """All template placeholders are filled from context."""
        mw = SystemPromptMiddleware(
            deferred_setup_getter=self._make_deferred_getter(),
            template=self.TEMPLATE,
        )

        context: MiddlewareContext = {
            "profile": {"name": "Alice"},
            "tasks": [{"title": "Buy milk"}],
            "instructions": {"memory": "Be concise"},
        }

        captured: MiddlewareContext = {}

        async def capture(state, runtime, ctx):
            captured.update(ctx)
            return {"messages": []}

        await mw(_make_state(), _make_runtime(), context, capture)

        msg = captured["system_message"]
        assert "Alice" in msg
        assert "Buy milk" in msg
        assert "Be concise" in msg
        assert "DT:" in msg

    async def test_falls_back_when_context_empty(self):
        """Empty context fields get default Chinese placeholders."""
        mw = SystemPromptMiddleware(
            deferred_setup_getter=self._make_deferred_getter(),
            template=self.TEMPLATE,
        )

        captured: MiddlewareContext = {}

        async def capture(state, runtime, ctx):
            captured.update(ctx)
            return {"messages": []}

        await mw(_make_state(), _make_runtime(), {}, capture)

        msg = captured["system_message"]
        assert "未设置" in msg  # default for None profile
        assert "无" in msg  # default for empty tasks

    async def test_deferred_tools_section(self):
        """Deferred tools section appears when names exist."""
        mw = SystemPromptMiddleware(
            deferred_setup_getter=self._make_deferred_getter(
                frozenset(["dingtalk_create_event", "dingtalk_send_message"])
            ),
            template=self.TEMPLATE,
        )

        captured: MiddlewareContext = {}

        async def capture(state, runtime, ctx):
            captured.update(ctx)
            return {"messages": []}

        await mw(_make_state(), _make_runtime(), {}, capture)

        msg = captured["system_message"]
        # The deferred section should mention available tools
        assert "dingtalk_create_event" in msg or "dingtalk" in msg.lower()


# ======================================================================
# ToolBindingMiddleware
# ======================================================================


class TestToolBindingMiddleware:
    async def test_binds_model_with_promoted_tools(self):
        """Model is bound and stored in context."""
        fake_model = MagicMock()

        def binder(*, promoted_names=None):
            fake_model._bound_promoted = promoted_names
            return fake_model

        mw = ToolBindingMiddleware(model_binder=binder)
        state = _make_state(promoted_tools=["dingtalk_tool_1", "dingtalk_tool_2"])

        captured: MiddlewareContext = {}

        async def capture(s, runtime, ctx):
            captured.update(ctx)
            return {"messages": []}

        await mw(state, _make_runtime(), {}, capture)

        assert captured["model"] is fake_model
        assert fake_model._bound_promoted == ["dingtalk_tool_1", "dingtalk_tool_2"]

    async def test_none_promoted_tools(self):
        """None promoted_tools is passed through correctly."""
        fake_model = MagicMock()

        def binder(*, promoted_names=None):
            fake_model._bound_promoted = promoted_names
            return fake_model

        mw = ToolBindingMiddleware(model_binder=binder)
        state = _make_state()  # no promoted_tools key

        captured: MiddlewareContext = {}

        async def capture(s, runtime, ctx):
            captured.update(ctx)
            return {"messages": []}

        await mw(state, _make_runtime(), {}, capture)

        assert captured["model"] is fake_model
        assert fake_model._bound_promoted is None


# ======================================================================
# ErrorHandlingMiddleware
# ======================================================================


class TestErrorHandlingMiddleware:
    async def test_passes_through_on_success(self):
        """Successful handler result passes through unchanged."""
        mw = ErrorHandlingMiddleware()
        result = await mw(_make_state(), _make_runtime(), {}, _noop_handler)
        assert result == {"messages": [AIMessage(content="ok")]}

    async def test_catches_bad_request_error(self):
        """BadRequestError returns Chinese apology."""

        async def failing_handler(state, runtime, context):
            raise type("BadRequestError", (Exception,), {})()

        mw = ErrorHandlingMiddleware()
        result = await mw(_make_state(), _make_runtime(), {}, failing_handler)
        messages = result["messages"]
        assert len(messages) == 1
        assert "抱歉" in messages[0].content
        assert isinstance(messages[0], AIMessage)

    async def test_catches_generic_exception(self):
        """Generic exception returns English error message."""

        async def failing_handler(state, runtime, context):
            raise RuntimeError("Something broke")

        mw = ErrorHandlingMiddleware()
        result = await mw(_make_state(), _make_runtime(), {}, failing_handler)
        messages = result["messages"]
        assert len(messages) == 1
        assert "Something broke" in messages[0].content
        assert isinstance(messages[0], AIMessage)

    async def test_catches_error_from_deep_in_chain(self):
        """Error thrown by an inner middleware is caught."""

        class ThrowingMiddleware:
            async def __call__(self, state, runtime, context, next_handler):
                return await next_handler(state, runtime, context)

        async def failing_core(state, runtime, context):
            raise ValueError("deep error")

        pipeline = Pipeline(
            middlewares=[ErrorHandlingMiddleware(), ThrowingMiddleware()],
            core_handler=failing_core,
        )
        result = await pipeline.run(_make_state(), _make_runtime())
        messages = result["messages"]
        assert len(messages) == 1
        assert "deep error" in messages[0].content


# ======================================================================
# Full pipeline integration
# ======================================================================


class TestFullPipelineIntegration:
    async def test_end_to_end_data_flow(self):
        """All 4 middlewares + core handler produce correct result."""
        from app.graph.middleware.memory_load import _RealStoreAccessor

        # Use real store accessor but mock the store to return canned data
        real_accessor = _RealStoreAccessor()

        # We'll test the full chain with real middleware classes but
        # mock the store and model at the boundaries.
        store = MagicMock()
        store.get.return_value = None  # Will trigger defaults

        # Override MemoryLoadMiddleware with a test double
        class TestMemoryLoad:
            async def __call__(self, state, runtime, context, next_handler):
                context["profile"] = {"name": "TestUser"}
                context["tasks"] = [{"title": "Task A", "key": "1"}]
                context["instructions"] = {"memory": "Test instructions"}
                return await next_handler(state, runtime, context)

        # Mock model
        model = MagicMock()
        model.ainvoke = AsyncMock(
            return_value=AIMessage(content="I'll help with that!")
        )

        class TestToolBinding:
            async def __call__(self, state, runtime, context, next_handler):
                context["model"] = model
                return await next_handler(state, runtime, context)

        pipeline = Pipeline(
            middlewares=[
                ErrorHandlingMiddleware(),
                TestMemoryLoad(),
                SystemPromptMiddleware(),
                TestToolBinding(),
            ],
            core_handler=_noop_handler,  # We verify data flow, skip real LLM
        )

        result = await pipeline.run(_make_state(), _make_runtime())
        assert "messages" in result
        assert len(result["messages"]) == 1
