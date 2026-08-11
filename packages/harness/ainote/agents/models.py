from dataclasses import dataclass
from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from ainote.config.model_factory import create_model_with_failover


@dataclass
class Configuration:
    """Runtime configuration for the agent graph."""

    user_id: str = "default"


@lru_cache
def get_model(
    name: str | None = None,
    temperature: float = 0.0,
) -> BaseChatModel:
    """Return a chat model selected by config.yaml (the single business entry).

    ``name`` selects a provider from ``config.yaml`` (None → ``active_model``).
    Builds the failover chain (primary + ``fallback_to`` backups) so transient
    provider errors (e.g. DeepSeek 503) fall through to a backup model.

    This is the only function the business layer calls — e.g.
    ``get_model()`` for TrustCall (thinking-disabled ``deepseek-chat``) and
    ``get_model("deepseek-reasoning")`` for the main chat path. The factory
    primitives (``create_model`` / ``create_model_with_failover``) stay in
    ``config/`` for tests and precise control.
    """
    return create_model_with_failover(name, temperature=temperature)
