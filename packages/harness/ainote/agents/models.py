from dataclasses import dataclass
from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from ainote.config.model_factory import create_model


@dataclass
class Configuration:
    """Runtime configuration for the agent graph."""

    user_id: str = "default"


@lru_cache
def get_model(temperature: float = 0.0) -> BaseChatModel:
    """Return the active chat model selected by config.yaml.

    Signature preserved for the call sites (tool_binder, TrustCall extractors,
    update_instructions) — all call with no arguments.
    """
    return create_model(temperature=temperature)
