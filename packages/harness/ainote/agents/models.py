from dataclasses import dataclass
from functools import lru_cache

from langchain_openai import ChatOpenAI

from ainote.config.settings import settings


@dataclass
class Configuration:
    """Runtime configuration for the agent graph."""

    user_id: str = "default"


@lru_cache
def get_model(temperature: float = 0.0) -> ChatOpenAI:
    """Return a configured chat model. Do not hard-code at module level."""
    # DeepSeek thinking mode (V4 Flash / R1) does not support
    # tool_choice="required" which TrustCall sends internally.
    # Disable thinking so TrustCall's PatchDoc / Task extraction works.
    extra_body = None
    if "deepseek" in settings.GLM_MODEL.lower():
        extra_body = {"thinking": {"type": "disabled"}}

    return ChatOpenAI(
        model=settings.GLM_MODEL,
        api_key=settings.GLM_API_KEY,
        base_url=settings.GLM_BASE_URL,
        temperature=temperature,
        model_kwargs={"extra_body": extra_body} if extra_body else None,
    )
