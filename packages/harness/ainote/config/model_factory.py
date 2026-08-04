"""Model-loading factory: build chat models from config.yaml.

Replaces the hardcoded ``ChatOpenAI`` + DeepSeek-thinking-disable in
``get_model()`` with a provider-registry factory. A single ``config.yaml``
declares multiple model providers; secrets are referenced by env-var name
(``api_key_env``) and resolved from ``.env`` at build time.

Key provider detail: ``ChatDeepSeek`` uses ``api_base`` (not ``base_url``)
to set its endpoint — passing ``base_url`` silently leaves the client on the
DeepSeek default URL. The registry builders below encode this correctly.
"""

import logging
import os
from pathlib import Path
from typing import Callable

import yaml
from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from ainote.config.model_provider import ModelConfigYAML, ModelProvider

logger = logging.getLogger(__name__)


# ── Provider builders ───────────────────────────────────────────────────


def _thinking_extra_body(cfg: ModelProvider) -> dict | None:
    """Return the extra_body to send, or None.

    DeepSeek reasoning models reject ``tool_choice="required"`` (used
    internally by TrustCall) while thinking is enabled, so we disable it
    unless the provider explicitly opts into thinking.
    """
    if cfg.provider == "deepseek" and not cfg.thinking:
        return {"thinking": {"type": "disabled"}}
    return None


def _build_openai(cfg: ModelProvider, *, api_key: str, temperature: float) -> ChatOpenAI:
    kwargs: dict = {
        "model": cfg.model,
        "api_key": api_key,
        "temperature": temperature,
    }
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    if cfg.max_tokens is not None:
        kwargs["max_tokens"] = cfg.max_tokens
    if extra := _thinking_extra_body(cfg):
        kwargs["extra_body"] = extra
    kwargs.update(cfg.model_kwargs)
    return ChatOpenAI(**kwargs)


def _build_deepseek(cfg: ModelProvider, *, api_key: str, temperature: float) -> ChatDeepSeek:
    kwargs: dict = {
        "model": cfg.model,
        "api_key": api_key,
        "temperature": temperature,
    }
    if cfg.base_url:
        kwargs["api_base"] = cfg.base_url   # ChatDeepSeek uses api_base, NOT base_url
    if cfg.max_tokens is not None:
        kwargs["max_tokens"] = cfg.max_tokens
    if extra := _thinking_extra_body(cfg):
        kwargs["extra_body"] = extra
    kwargs.update(cfg.model_kwargs)
    return ChatDeepSeek(**kwargs)


# provider name → builder callable. Add a new provider = add one line here
# (and an entry in config.yaml); no other code changes.
PROVIDER_REGISTRY: dict[str, Callable[..., BaseChatModel]] = {
    "openai": _build_openai,
    "deepseek": _build_deepseek,
}


# ── Config loading ──────────────────────────────────────────────────────

_loaded: ModelConfigYAML | None = None


def _find_config_path() -> Path | None:
    """Locate config.yaml: explicit env override, then cwd, then backend root."""
    env = os.getenv("AINOTE_CONFIG_YAML")
    if env and Path(env).is_file():
        return Path(env)
    for cand in (
        Path.cwd() / "config.yaml",
        # model_factory.py sits at backend/packages/harness/ainote/config/,
        # so parents[4] is the backend root regardless of CWD.
        Path(__file__).resolve().parents[4] / "config.yaml",
    ):
        if cand.is_file():
            return cand
    return None


def _legacy_env_config() -> ModelConfigYAML:
    """Backward-compat: build config from the existing GLM_* env vars.

    This is a fallback only, used when no config.yaml is present. It
    reproduces today's behavior (deepseek model name → thinking disabled).
    """
    from ainote.config.model_config import get_model_config

    logger.warning(
        "config.yaml not found; falling back to legacy GLM_* env config. "
        "Create config.yaml to use the multi-provider factory."
    )
    legacy = get_model_config()
    is_deepseek = "deepseek" in legacy.GLM_MODEL.lower()
    return ModelConfigYAML(
        active_model=legacy.GLM_MODEL,
        model_providers=[
            ModelProvider(
                name=legacy.GLM_MODEL,
                provider="deepseek" if is_deepseek else "openai",
                model=legacy.GLM_MODEL,
                base_url=legacy.GLM_BASE_URL,
                api_key_env="GLM_API_KEY",
                temperature=0.0,
                thinking=not is_deepseek,  # deepseek → disabled, GLM → no-op
            )
        ],
    )


def get_model_config_yaml() -> ModelConfigYAML:
    """Return the validated config.yaml content (cached per process)."""
    global _loaded
    if _loaded is not None:
        return _loaded
    path = _find_config_path()
    if path is None:
        _loaded = _legacy_env_config()
        return _loaded
    _loaded = ModelConfigYAML.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
    return _loaded


def _reset_config_cache() -> None:
    """Clear the module-level config cache (test hook)."""
    global _loaded
    _loaded = None


def _resolve_api_key(cfg: ModelProvider) -> str:
    """Read the secret from the env var named by ``cfg.api_key_env``."""
    if not cfg.api_key_env:
        raise ValueError(f"provider '{cfg.name}' has no api_key_env configured")
    load_dotenv()  # idempotent; ensures .env is loaded even outside app.main
    key = os.getenv(cfg.api_key_env, "").strip()
    if not key:
        raise ValueError(
            f"api_key_env '{cfg.api_key_env}' for provider '{cfg.name}' is not set "
            f"or empty (put it in .env, not config.yaml)"
        )
    return key


# ── Public entry point ─────────────────────────────────────────────────


def create_model(
    name: str | None = None,
    *,
    temperature: float | None = None,
) -> BaseChatModel:
    """Build a chat model for the named provider (or ``active_model``).

    ``temperature`` overrides the per-provider value from config.yaml.
    """
    cfg = get_model_config_yaml()
    target = name or cfg.active_model
    provider_cfg = next((p for p in cfg.model_providers if p.name == target), None)
    if provider_cfg is None:
        raise ValueError(f"unknown model provider name '{target}'")
    builder = PROVIDER_REGISTRY.get(provider_cfg.provider)
    if builder is None:
        raise ValueError(
            f"provider '{provider_cfg.provider}' is not registered "
            f"(known: {sorted(PROVIDER_REGISTRY)})"
        )
    api_key = _resolve_api_key(provider_cfg)
    temp = temperature if temperature is not None else provider_cfg.temperature
    return builder(provider_cfg, api_key=api_key, temperature=temp)
