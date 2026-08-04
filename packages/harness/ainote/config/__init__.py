"""Focused config modules (deer-flow singleton pattern).

Each module exposes a pydantic-settings class and a ``get_*_config()``
singleton getter. For backward compatibility, ``app.config.settings``
still works as a unified proxy.
"""

from ainote.config.app_config import AppConfig, get_app_config
from ainote.config.database_config import DatabaseConfig, get_database_config
from ainote.config.model_config import ModelConfig, get_model_config
from ainote.config.tool_config import ToolConfig, get_tool_config
from ainote.config.model_provider import ModelConfigYAML, ModelProvider
from ainote.config.model_factory import PROVIDER_REGISTRY, create_model, get_model_config_yaml

__all__ = [
    "AppConfig",
    "DatabaseConfig",
    "ModelConfig",
    "ToolConfig",
    "ModelProvider",
    "ModelConfigYAML",
    "PROVIDER_REGISTRY",
    "get_app_config",
    "get_database_config",
    "get_model_config",
    "get_tool_config",
    "get_model_config_yaml",
    "create_model",
]
