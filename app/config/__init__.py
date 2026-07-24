"""Focused config modules (deer-flow singleton pattern).

Each module exposes a pydantic-settings class and a ``get_*_config()``
singleton getter. For backward compatibility, ``app.config.settings``
still works as a unified proxy.
"""

from app.config.app_config import AppConfig, get_app_config
from app.config.database_config import DatabaseConfig, get_database_config
from app.config.model_config import ModelConfig, get_model_config
from app.config.tool_config import ToolConfig, get_tool_config

__all__ = [
    "AppConfig",
    "DatabaseConfig",
    "ModelConfig",
    "ToolConfig",
    "get_app_config",
    "get_database_config",
    "get_model_config",
    "get_tool_config",
]
