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
    max_retries: int | None = None  # override OpenAI SDK default (2)
    fallback_to: str | None = None  # backup provider name for transient-error failover
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

        name_set = set(names)
        # fallback_to must point at an existing provider, never itself.
        for p in self.model_providers:
            if p.fallback_to is not None:
                if p.fallback_to == p.name:
                    raise ValueError(f"provider '{p.name}' cannot fall back to itself")
                if p.fallback_to not in name_set:
                    raise ValueError(
                        f"provider '{p.name}' fallback_to '{p.fallback_to}' "
                        f"is not in model_providers"
                    )

        # Reject fallback_to cycles (a → b → a).
        graph = {p.name: p.fallback_to for p in self.model_providers}
        for start in graph:
            seen: set[str] = set()
            cur = graph[start]
            while cur is not None:
                if cur == start or cur in seen:
                    raise ValueError(
                        f"fallback_to cycle detected involving '{start}'"
                    )
                seen.add(cur)
                cur = graph.get(cur)
        return self
