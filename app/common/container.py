"""Lifespan-managed application context (the DI container).

All long-lived singletons — the DB pool, memory store, checkpointer, the
compiled graph, and the DingTalk runtime registry — are created exactly once
inside the FastAPI lifespan and exposed on ``app.state.app_context``. Request
handlers resolve them via the ``Depends`` getters in ``app/common/dependencies.py``.

Why this shape:
- No ``import``-time side effects: importing a module never opens a DB pool,
  compiles a graph, or draws graph.png.
- No service-locator reads (``builder.store`` scattered across 8+ modules):
  every consumer declares its dependency and FastAPI injects it.
- Shutdown is symmetrical: components are closed in the same order they were
  created, inside the ``finally``.

Graph-internal code (nodes / middleware / binder / ScopedToolNode) executes
OUTSIDE request scope, so it cannot use ``Depends``. Those call sites keep
calling the module-level functions of ``ainote.agents.graph.dingtalk_runtime``,
which delegate to the ``DingTalkRuntime`` instance installed here via
``configure_runtime`` — the documented indirection to a lifecycle-managed
object pattern.
"""

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from ainote.agents.graph import builder
from ainote.agents.graph.dingtalk_runtime import DingTalkRuntime, configure_runtime

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Every lifecycle-managed component of one app process.

    ``pool`` is ``None`` when running on the in-memory store (DATABASE_URL
    unset or connection failed). ``dingtalk`` is the per-user DingTalk MCP
    runtime registry (owns the same ``store``).
    """

    pool: object | None
    store: object
    checkpointer: object
    graph: object
    dingtalk: DingTalkRuntime


@asynccontextmanager
async def create_app_context() -> AsyncIterator[AppContext]:
    """Create every component, wire the DingTalk runtime, and yield the context.

    Any failure — e.g. a connected-but-broken Postgres store (data-loss risk)
    — propagates out of the lifespan and fails startup loudly, rather than
    silently degrading to in-memory.
    """
    store, checkpointer, pool = builder.create_runtime()
    graph = builder.build_graph(store=store, checkpointer=checkpointer)

    # Install the per-user DingTalk runtime registry. It owns the same store,
    # and graph internals reach it through the module-level accessor.
    dingtalk = DingTalkRuntime(store=store)
    configure_runtime(dingtalk)

    # 绘制并保存 graph 结构图（本地调试；容器/生产用 SKIP_GRAPH_PNG=1 跳过）。
    builder.save_graph_diagram(graph)

    ctx = AppContext(
        pool=pool,
        store=store,
        checkpointer=checkpointer,
        graph=graph,
        dingtalk=dingtalk,
    )
    logger.info(
        "AppContext initialized — store=%s pool=%s",
        type(store).__name__,
        "postgresql" if pool is not None else "memory",
    )
    try:
        yield ctx
    finally:
        # Shutdown in reverse order of creation.
        dingtalk.close()
        if pool is not None:
            try:
                pool.close()
            except Exception:  # pragma: no cover - best-effort shutdown
                logger.exception("Failed to close PostgreSQL connection pool")
