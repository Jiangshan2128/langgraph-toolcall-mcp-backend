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

from ainote.config import model_factory
from ainote.config.model_factory import (
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
  - name: glm
    provider: openai
    model: glm-5.1
    base_url: https://open.bigmodel.cn/api/paas/v4/
    api_key_env: GLM_API_KEY
    temperature: 0.0
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


def test_get_model_signature_unchanged():
    from ainote.agents.models import get_model

    get_model.cache_clear()
    # With the real backend config.yaml + .env present, get_model() must
    # still return a BaseChatModel (ChatOpenAI or ChatDeepSeek).
    m = get_model()
    assert isinstance(m, (ChatOpenAI, ChatDeepSeek))
    get_model.cache_clear()
