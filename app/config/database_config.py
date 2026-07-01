"""Database connection settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseSettings):
    DATABASE_URL: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


_database_config: DatabaseConfig | None = None


def get_database_config() -> DatabaseConfig:
    """Return the cached ``DatabaseConfig`` singleton."""
    global _database_config
    if _database_config is None:
        _database_config = DatabaseConfig()
    return _database_config
