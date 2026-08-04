"""Pydantic models for the multi-provider model configuration (config.yaml).

These models validate the structured YAML that drives the model factory. The
YAML file only ever stores the *name* of an environment variable
(``api_key_env``); the real secret lives in ``.env`` — never in the YAML.
"""

from pydantic import BaseModel, Field, model_validator


class ModelProvider(BaseModel):
    """A single model provider entry in ``model_providers``."""

    name: str                      # unique key referenced by ``active_model``
    provider: str                  # registry key: "openai", "deepseek", ...
    model: str                     # actual model id sent to the API
    base_url: str | None = None    # OpenAI-compatible endpoint
    api_key_env: str = ""          # env var NAME holding the secret — never a literal key
    temperature: float = 0.0
    thinking: bool = False         # False → send extra_body thinking-disabled (DeepSeek)
    max_tokens: int | None = None
    model_kwargs: dict = Field(default_factory=dict)  # optional arbitrary passthrough


class ModelConfigYAML(BaseModel):
    """Top-level config.yaml structure."""

    active_model: str
    model_providers: list[ModelProvider]

    @model_validator(mode="after")
    def _validate(self):
        if not self.model_providers:
            raise ValueError("model_providers must contain at least one entry")
        names = [p.name for p in self.model_providers]
        if self.active_model not in names:
            raise ValueError(
                f"active_model '{self.active_model}' is not in model_providers "
                f"(available: {sorted(set(names))})"
            )
        dupes = {n for n in set(names) if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate provider names: {sorted(dupes)}")
        return self
