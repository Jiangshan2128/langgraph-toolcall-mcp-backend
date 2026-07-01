"""Backward-compatible settings proxy.

Delegates to the focused config modules under ``app.config.*``.
Existing code that does ``from app.core.config import settings``
continues to work without changes.

New code should import directly from the config modules::

    from app.config import get_model_config
    model_cfg = get_model_config()
    print(model_cfg.GLM_MODEL)
"""

from app.config.app_config import get_app_config
from app.config.database_config import get_database_config
from app.config.model_config import get_model_config
from app.config.tool_config import get_tool_config

_GETTERS = (
    get_app_config,
    get_model_config,
    get_database_config,
    get_tool_config,
)


class _Settings:
    """Unified settings proxy — delegates attribute lookups to config modules.

    Each ``settings.X`` access iterates the config singletons and returns
    the first match.  This is intentionally simple; typed access should
    use the individual ``get_*_config()`` functions directly.
    """

    def __getattr__(self, name: str):
        for getter in _GETTERS:
            cfg = getter()
            if hasattr(cfg, name):
                return getattr(cfg, name)
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )

    def __repr__(self) -> str:
        return "<settings proxy — use get_*_config() for typed access>"


settings = _Settings()
