"""Tests for the model factory (config.yaml-driven multi-provider loading).

These tests exercise ``create_model`` and the provider builders directly
(avoiding the ``@lru_cache`` on ``get_model``) with a temp YAML pointed at via
``AINOTE_CONFIG_YAML``.
"""

import os
from pathlib import Path

import pytest
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from ainote.agents.graph.model import model_factory
from ainote.agents.graph.model.model_factory import (
    create_model,
    get_model_config_yaml,
    _reset_config_cache,
)


DEEPSEEK_YAML = """
active_model: deepseek-chat
model_providers:
  - name: deepseek-chat
    provider: deepseek
    model: deepseek-v4-flash
    base_url: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    temperature: 0.0
    thinking: false
    max_retries: 3
    fallback_to: glm
  - name: glm
    provider: openai
    model: glm-5.1
    base_url: https://open.bigmodel.cn/api/paas/v4/
    api_key_env: GLM_API_KEY
    temperature: 0.0
    max_retries: 5
"""


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Write a temp config.yaml and point AINOTE_CONFIG_YAML at it."""
    p = tmp_path / "config.yaml"
    p.write_text(DEEPSEEK_YAML, encoding="utf-8")
    monkeypatch.setenv("AINOTE_CONFIG_YAML", str(p))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")
    monkeypatch.setenv("GLM_API_KEY", "test-glm-key")
    _reset_config_cache()
    yield p
    _reset_config_cache()


def test_deepseek_builder_api_base_and_thinking(tmp_config):
    m = create_model("deepseek-chat")
    assert isinstance(m, ChatDeepSeek)
    # ChatDeepSeek must receive api_base, not base_url
    assert m.api_base == "https://api.deepseek.com"
    # thinking: false → extra_body thinking disabled
    assert m.extra_body == {"thinking": {"type": "disabled"}}


def test_openai_builder_base_url_and_no_extra_body(tmp_config):
    m = create_model("glm")
    assert isinstance(m, ChatOpenAI)
    assert m.openai_api_base == "https://open.bigmodel.cn/api/paas/v4/"
    assert m.extra_body is None


def test_create_model_dispatch_and_temperature(tmp_config):
    assert isinstance(create_model(), ChatDeepSeek)  # active_model
    m = create_model("glm", temperature=0.7)
    assert isinstance(m, ChatOpenAI)
    assert m.temperature == 0.7


def test_active_model_must_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bad_yaml = """
active_model: nope
model_providers:
  - name: glm
    provider: openai
    model: glm-5.1
    api_key_env: GLM_API_KEY
"""
    p = tmp_path / "config.yaml"
    p.write_text(bad_yaml, encoding="utf-8")
    monkeypatch.setenv("AINOTE_CONFIG_YAML", str(p))
    _reset_config_cache()
    with pytest.raises(ValidationError):
        get_model_config_yaml()
    _reset_config_cache()


def test_unknown_provider_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bad_yaml = """
active_model: x
model_providers:
  - name: x
    provider: does-not-exist
    model: m
    api_key_env: GLM_API_KEY
"""
    p = tmp_path / "config.yaml"
    p.write_text(bad_yaml, encoding="utf-8")
    monkeypatch.setenv("AINOTE_CONFIG_YAML", str(p))
    _reset_config_cache()
    with pytest.raises(ValueError, match="not registered"):
        create_model()
    _reset_config_cache()


def test_missing_api_key_env_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yaml_str = """
active_model: x
model_providers:
  - name: x
    provider: openai
    model: m
    api_key_env: NOPE_UNSET_VAR
"""
    p = tmp_path / "config.yaml"
    p.write_text(yaml_str, encoding="utf-8")
    monkeypatch.setenv("AINOTE_CONFIG_YAML", str(p))
    _reset_config_cache()
    with pytest.raises(ValueError, match="not set or empty"):
        create_model()
    _reset_config_cache()


def test_legacy_fallback_deepseek(monkeypatch: pytest.MonkeyPatch):
    """No config.yaml → fall back to GLM_* env; deepseek name disables thinking."""

    class FakeLegacy:
        GLM_MODEL = "deepseek-v4-flash"
        GLM_BASE_URL = "https://api.deepseek.com"

    monkeypatch.delenv("AINOTE_CONFIG_YAML", raising=False)
    _reset_config_cache()
    monkeypatch.setattr(
        "ainote.config.model_config.get_model_config",
        lambda: FakeLegacy(),
    )
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    cfg = get_model_config_yaml()
    provider = cfg.model_providers[0]
    assert provider.provider == "deepseek"
    assert provider.thinking is False  # deepseek → thinking disabled
    _reset_config_cache()


def test_get_model_is_single_business_entry():
    """get_model() is the only entry the business layer uses; it accepts name."""
    from ainote.agents.graph.model.model import get_model
    from langchain_core.language_models import BaseChatModel
    from ainote.agents.graph.model.model_factory import create_model

    get_model.cache_clear()
    # No name → active_model (deepseek-chat); named → that provider.
    # Both must be BaseChatModel (raw or failover wrapper).
    assert isinstance(get_model(), BaseChatModel)
    assert isinstance(get_model("deepseek-reasoning"), BaseChatModel)
    # Named call must resolve to the same provider as the factory's raw build
    # (unwrapping the failover wrapper when present).
    named = get_model("deepseek-reasoning")
    primary = named.models[0] if hasattr(named, "models") else named
    assert isinstance(primary, type(create_model("deepseek-reasoning")))
    get_model.cache_clear()


# ── Failover tests ──────────────────────────────────────────────────────


def test_failover_chain_builds(tmp_config):
    from ainote.agents.graph.model.model_factory import create_model_with_failover
    from ainote.agents.graph.model.model_failover import FailoverChatModel

    m = create_model_with_failover("deepseek-chat")
    assert isinstance(m, FailoverChatModel)
    assert len(m.models) == 2
    assert isinstance(m.models[0], ChatDeepSeek)   # primary
    assert isinstance(m.models[1], ChatOpenAI)     # fallback glm


def test_no_fallback_returns_raw_model(tmp_config):
    from ainote.agents.graph.model.model_factory import create_model_with_failover

    m = create_model_with_failover("glm")
    assert isinstance(m, ChatOpenAI)  # glm has no fallback → raw model


def test_max_retries_plumbed(tmp_config):
    # deepseek-chat has max_retries: 3, glm has max_retries: 5
    m = create_model("deepseek-chat")
    assert m.max_retries == 3
    m2 = create_model("glm")
    assert m2.max_retries == 5


def test_failover_bind_tools_and_invoke(tmp_config, monkeypatch):
    """Primary raises 503 → backup answers; both raw and bound paths."""
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from ainote.agents.graph.model.model_factory import create_model_with_failover
    from ainote.agents.graph.model.model_failover import FailoverChatModel

    primary = create_model("deepseek-chat")
    backup = create_model("glm")
    failover = FailoverChatModel(models=[primary, backup])

    # Primary's _generate/_agenerate raise InternalServerError (503)
    def _boom(*a, **k):
        import httpx
        import openai
        req = httpx.Request("POST", "http://x")
        raise openai.InternalServerError(
            "503",
            response=httpx.Response(503, request=req),
            body={"error": {"message": "Service is too busy"}},
        )

    async def _boom_async(*a, **k):
        _boom(*a, **k)

    monkeypatch.setattr(primary, "_generate", _boom)
    monkeypatch.setattr(primary, "_agenerate", _boom_async)

    def _ok(*a, **k):
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="ok from backup"))]
        )

    async def _ok_async(*a, **k):
        return _ok(*a, **k)

    monkeypatch.setattr(backup, "_generate", _ok)
    monkeypatch.setattr(backup, "_agenerate", _ok_async)

    # Raw invoke path
    res = failover.invoke([HumanMessage(content="hi")])
    assert res.content == "ok from backup"

    # Raw ainvoke path
    import asyncio
    res = asyncio.run(failover.ainvoke([HumanMessage(content="hi")]))
    assert res.content == "ok from backup"

    # Bound path (bind_tools → runnable → invoke)
    bound = failover.bind_tools([{"type": "function", "function": {"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}}])
    res = bound.invoke([HumanMessage(content="hi")])
    assert res.content == "ok from backup"


def test_non_transient_error_propagates(tmp_config, monkeypatch):
    """BadRequestError (400) must propagate — no fallback on permanent errors."""
    from langchain_core.messages import HumanMessage
    from ainote.agents.graph.model.model_factory import create_model
    from ainote.agents.graph.model.model_failover import FailoverChatModel

    primary = create_model("deepseek-chat")
    backup = create_model("glm")
    failover = FailoverChatModel(models=[primary, backup])

    def _boom(*a, **k):
        import httpx
        import openai
        req = httpx.Request("POST", "http://x")
        raise openai.BadRequestError(
            "400",
            response=httpx.Response(400, request=req),
            body={"error": {"message": "bad"}},
        )

    monkeypatch.setattr(primary, "_generate", _boom)
    monkeypatch.setattr(backup, "_generate", lambda *a, **k: None)  # should not be reached

    import httpx
    import openai
    with pytest.raises(openai.BadRequestError):
        failover.invoke([HumanMessage(content="hi")])


def test_fallback_cycle_rejected(tmp_path, monkeypatch):
    yaml_str = """
active_model: a
model_providers:
  - name: a
    provider: openai
    model: m
    api_key_env: GLM_API_KEY
    fallback_to: b
  - name: b
    provider: openai
    model: m2
    api_key_env: GLM_API_KEY
    fallback_to: a
"""
    p = tmp_path / "config.yaml"
    p.write_text(yaml_str, encoding="utf-8")
    monkeypatch.setenv("AINOTE_CONFIG_YAML", str(p))
    _reset_config_cache()
    with pytest.raises(ValidationError):
        get_model_config_yaml()
    _reset_config_cache()


def test_fallback_to_unknown_rejected(tmp_path, monkeypatch):
    yaml_str = """
active_model: a
model_providers:
  - name: a
    provider: openai
    model: m
    api_key_env: GLM_API_KEY
    fallback_to: nope
"""
    p = tmp_path / "config.yaml"
    p.write_text(yaml_str, encoding="utf-8")
    monkeypatch.setenv("AINOTE_CONFIG_YAML", str(p))
    _reset_config_cache()
    with pytest.raises(ValidationError):
        get_model_config_yaml()
    _reset_config_cache()
