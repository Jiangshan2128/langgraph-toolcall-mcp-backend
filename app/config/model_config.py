"""LLM provider settings (GLM via OpenAI-compatible API)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseSettings):
    # GLM (ZhiPu AI) — primary provider
    GLM_API_KEY: str = ""
    GLM_MODEL: str = "glm-4.5-air"
    GLM_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4/"
    GLM_MAX_TOOLS: int = 0  # 0 = disabled, >0 = tool routing threshold

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


_model_config: ModelConfig | None = None


def get_model_config() -> ModelConfig:
    """Return the cached ``ModelConfig`` singleton."""
    global _model_config
    if _model_config is None:
        _model_config = ModelConfig()
    return _model_config
