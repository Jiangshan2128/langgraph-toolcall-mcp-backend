"""Transient-error failover wrapper for chat models.

Wraps a chain of chat models so that transient provider errors (5xx, 429,
timeouts, connection errors) fall through to a backup model instead of failing
the request. This is a ``BaseChatModel`` subclass so it can be passed to
TrustCall's ``create_extractor`` (which binds tools internally) and used
anywhere a raw model is expected.

Non-transient errors (400/401/403) deliberately propagate — a schema rejection
or auth failure will not recover on another provider, and falling back on them
would mask real bugs.
"""

import logging

import httpx
import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableWithFallbacks
from pydantic import ConfigDict, Field

logger = logging.getLogger(__name__)

# Transient errors that may recover on a backup provider.
# Deliberately excludes BadRequestError(400), AuthenticationError(401),
# PermissionDeniedError(403) — those won't recover on another provider.
TRANSIENT_API_ERRORS: tuple[type[BaseException], ...] = (
    openai.InternalServerError,  # 5xx, incl. 503 "Service is too busy"
    openai.RateLimitError,       # 429
    openai.APITimeoutError,      # request timeout
    openai.APIConnectionError,   # connection failed
    httpx.TransportError,        # raw httpx connect/timeout at a lower layer
)


class _FailoverRunnable(RunnableWithFallbacks):
    """``RunnableWithFallbacks`` that also exposes ``.bound``.

    TrustCall's ``_ExtractUpdates`` reads ``.bound.bound`` when ``enable_deletes``
    is enabled (not used by any current call site). Exposing the primary bound
    runnable keeps that path working; it degrades to re-binding on the primary
    model only, which matches today's behavior for that rare edge.
    """

    @property
    def bound(self) -> object:
        return self.runnable


class FailoverChatModel(BaseChatModel):
    """A chat model that tries a chain of models on transient errors.

    ``models[0]`` is primary; later entries are fallbacks tried in order.
    ``bind_tools`` returns a runnable that tries the primary-bound runnable
    first and falls back to the backup-bound runnables — so TrustCall (which
    binds tools internally) gets failover transparently. Raw ``invoke``/
    ``ainvoke`` (no tools) loop the models in ``_generate``/``_agenerate``.
    """

    models: list[BaseChatModel] = Field(default_factory=list)
    exceptions_to_handle: tuple[type[BaseException], ...] = TRANSIENT_API_ERRORS
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "failover_chat_model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        """Bind tools on every model; failover across the bound runnables.

        Delegates tool formatting / tool_choice resolution to each underlying
        model's own ``bind_tools`` (provider-correct per model).
        """
        runnables = [
            m.bind_tools(tools, tool_choice=tool_choice, **kwargs)
            for m in self.models
        ]
        return _FailoverRunnable(
            runnable=runnables[0],
            fallbacks=runnables[1:],
            exceptions_to_handle=self.exceptions_to_handle,
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """Try each model's ``_generate``; fall through on transient errors."""
        last_exc: BaseException | None = None
        for i, m in enumerate(self.models):
            try:
                return m._generate(
                    messages, stop=stop, run_manager=run_manager, **kwargs
                )
            except self.exceptions_to_handle as exc:
                logger.warning("model[%d] failed (%s); trying fallback", i, exc)
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("no models configured for FailoverChatModel")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        """Try each model's ``_agenerate``; fall through on transient errors."""
        last_exc: BaseException | None = None
        for i, m in enumerate(self.models):
            try:
                return await m._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs
                )
            except self.exceptions_to_handle as exc:
                logger.warning("model[%d] failed (%s); trying fallback", i, exc)
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("no models configured for FailoverChatModel")
