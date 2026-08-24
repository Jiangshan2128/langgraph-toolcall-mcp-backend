"""LangGraph-level fault tolerance for the agent graph.

Single source of truth for the three composable mechanisms LangGraph gives us
(see https://docs.langchain.com/oss/python/langgraph/fault-tolerance):

* **Retries** — ``RETRY_POLICY`` re-runs a failed node attempt on transient
  errors (network blips, rate limits, 5xx, timeouts) with exponential backoff.
* **Timeouts** — per-node ``TimeoutPolicy`` caps how long a single attempt may
  run before ``NodeTimeoutError`` is raised (retryable by default).
* **Error handling** — ``graph_error_handler`` runs as a compensation branch
  after a node's retries are exhausted, turning the final exception into a
  friendly user message instead of bubbling a 500 to the HTTP layer.

Applied once for every node via ``StateGraph.set_node_defaults`` in
``builder.py`` (requires langgraph>=1.2; the project locks 1.2.2).

Why a custom ``retry_on`` instead of LangGraph's ``default_retry_on``:

* ``default_retry_on`` retries ``ConnectionError``, httpx/requests 5xx and
  OpenAI/Groq-style SDK errors, but **excludes ``OSError`` subclasses** —
  including ``TimeoutError`` / ``asyncio.TimeoutError``, exactly the transient
  failure we most want to absorb.
* OpenAI/Groq ``APIStatusError`` instances fall *through* the default predicate
  (not in its exclusion tuple), so a 4xx like ``BadRequestError`` would be
  retried pointlessly. We make status-code-aware decisions for those instead.

This module imports nothing from the node/builder layers, so it can be imported
by ``builder.py`` without creating import cycles.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage
from langgraph.errors import NodeError
from langgraph.types import RetryPolicy, TimeoutPolicy, default_retry_on

from ainote.agents.graph.state import AgentState

logger = logging.getLogger(__name__)


def retry_on(exc: Exception) -> bool:
    """Return True when ``exc`` is a transient failure worth retrying.

    Order matters: OpenAI/Groq SDK errors are checked before falling through to
    ``default_retry_on`` because they would otherwise be treated as retryable
    regardless of status code.
    """
    mro = {c.__name__ for c in type(exc).__mro__}

    # OpenAI / Groq / compatible SDK status errors (openai.APIStatusError,
    # groq.APIStatusError, ...): retry only rate limits and 5xx. 4xx (bad
    # request, auth, not-found) are fatal — retrying can never fix them.
    if "APIStatusError" in mro:
        status = getattr(exc, "status_code", None)
        return status is None or status == 429 or 500 <= status < 600

    # langchain-core BadRequestError (not an APIStatusError): fatal, mirrors
    # the graph error handler's bad-request path.
    if "BadRequestError" in mro:
        return False

    # TimeoutError / asyncio.TimeoutError are OSError subclasses, which
    # default_retry_on excludes — but they are precisely transient.
    if isinstance(exc, TimeoutError):
        return True

    return default_retry_on(exc)


# 3 attempts total (1 original + 2 retries), exponential backoff, jittered.
RETRY_POLICY = RetryPolicy(max_attempts=3, retry_on=retry_on)


def _is_bad_request(exc: Exception) -> bool:
    """True for model-provider bad-request failures (fatal, never retried).

    Matches langchain-core ``BadRequestError`` / any ``BadRequestError``-named
    SDK error, or an exception carrying a 400 status — the same classification
    the old ``ErrorHandlingMiddleware`` used, so user-facing text is unchanged.
    """
    if "BadRequestError" in type(exc).__name__:
        return True
    status = getattr(exc, "status_code", None)
    if status == 400:
        return True
    return "400" in str(exc)


def graph_error_handler(state: AgentState, error: NodeError) -> dict:
    """Compensation after a node's retries are exhausted.

    Runs at the graph level (registered via ``set_node_defaults``) whenever a
    node raises a **non-retryable** error (immediately — no retries are spent)
    or a retryable error that exhausted its attempts. Returns a user-facing
    message so the turn degrades gracefully instead of raising to the HTTP
    layer.

    Bad-request failures (model-provider 400 / ``BadRequestError``) get a
    provider-specific apology; everything else gets a generic one. The raw
    exception is logged server-side (with the failing node name) but never
    echoed to the client — consistent with ``chat_stream``, which avoids
    leaking connection strings / key fragments in ``str(exc)``.
    """
    logger.error(
        "Graph node '%s' failed after retries exhausted: %s (%s)",
        error.node,
        type(error.error).__name__,
        error.error,
    )
    if _is_bad_request(error.error):
        content = "抱歉，模型服务暂时不可用，请稍后重试。"
    else:
        content = "抱歉，服务暂时不可用，请稍后重试。"
    return {"messages": [AIMessage(content=content)]}


# ── Per-node timeouts ────────────────────────────────────────────────────
# Applied explicitly in builder.py (values differ per node, so they are not
# set via set_node_defaults). idle_timeout resets on progress signals (LLM
# tokens, tool callbacks), so long but productive work is not cut off.

# LLM call incl. SDK-level retries; a single completion rarely exceeds 2 min.
AGENT_TIMEOUT = TimeoutPolicy(run_timeout=300, idle_timeout=120)

# Tool superstep runs tools concurrently; slowest tool bounds wall-clock.
TOOLS_TIMEOUT = TimeoutPolicy(run_timeout=240, idle_timeout=90)

# Chunked long-audio transcription can take several minutes legitimately.
TRANSCRIPTION_TIMEOUT = TimeoutPolicy(run_timeout=600, idle_timeout=180)
