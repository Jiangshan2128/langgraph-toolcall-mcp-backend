"""Focused config modules (deer-flow singleton pattern).

Each module exposes a pydantic-settings class and a ``get_*_config()``
singleton getter. For backward compatibility, ``app.config.settings``
still works as a unified proxy.

Model-provider config lives in the agent layer instead: see
``ainote.agents.graph.model`` (``model_factory`` / ``model_failover`` /
``model_provider``). ``model_config`` here keeps the legacy GLM_* env config.
"""

from ainote.config.app_config import AppConfig, get_app_config
from ainote.config.database_config import DatabaseConfig, get_database_config
from ainote.config.model_config import ModelConfig, get_model_config
from ainote.config.tool_config import ToolConfig, get_tool_config
from ainote.config.auth_config import AuthConfig, get_auth_config

__all__ = [
    "AppConfig",
    "DatabaseConfig",
    "ModelConfig",
    "ToolConfig",
    "AuthConfig",
    "get_app_config",
    "get_database_config",
    "get_model_config",
    "get_tool_config",
    "get_auth_config",
]
