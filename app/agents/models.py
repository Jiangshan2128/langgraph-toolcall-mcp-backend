from dataclasses import dataclass
from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.config.settings import settings


@dataclass
class Configuration:
    """Runtime configuration for the agent graph."""

    user_id: str = "default"


@lru_cache
def get_model(temperature: float = 0.0) -> ChatOpenAI:
    """Return a configured chat model. Do not hard-code at module level."""
    return ChatOpenAI(
        model=settings.GLM_MODEL,
        api_key=settings.GLM_API_KEY,
        base_url=settings.GLM_BASE_URL,
        temperature=temperature,
    )
