"""Application-level settings (title, version)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    APP_TITLE: str = "Banana Todo List Backend"
    APP_VERSION: str = "0.1.0"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


_app_config: AppConfig | None = None


def get_app_config() -> AppConfig:
    """Return the cached ``AppConfig`` singleton."""
    global _app_config
    if _app_config is None:
        _app_config = AppConfig()
    return _app_config
