"""Pure graph / persistence factories — NO import-time side effects.

The old module created ``pool`` / ``store`` / ``checkpointer`` globals at import
time (opening the DB pool, running the store health check, compiling the graph,
and drawing graph.png on every import). That made any ``import`` — including a
unit test — boot the whole stack and connect to the database.

Now the lifespan-managed container (``app/common/container.py``) calls these
factories explicitly and exposes the results on ``app.state.app_context``.
Request handlers resolve components via the ``Depends`` getters in
``app/common/dependencies.py`` — never ``import builder`` and reach for a
module global.
"""

import logging
import os

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.store.memory import InMemoryStore

from ainote.agents.models import Configuration
from ainote.config.settings import settings
from ainote.agents.graph.nodes import hitl_node, make_agent_node
from ainote.agents.graph.routing import route_after_agent, route_after_tools, route_start
from ainote.agents.graph.scoped_tool_node import ScopedToolNode
from ainote.agents.graph.state import AgentState
from ainote.tools import ALL_TOOLS
from ainote.transcription.graph import transcription_subgraph

logger = logging.getLogger(__name__)


class _FatalStoreError(RuntimeError):
    """Store is connected but not functional — treat as fatal (data loss risk)."""


def create_runtime():
    """Create ``(store, checkpointer, pool)`` once, preserving the historical
    failure semantics in one place.

    - ``DATABASE_URL`` unset → ``(InMemoryStore, MemorySaver, None)``.
    - ``DATABASE_URL`` set but unreachable → warn and fall back to in-memory
      (the operator sees the log; cold start keeps working).
    - Connected but broken store (health check fails) → raise
      ``_FatalStoreError``: never silently fall back to memory, which would
      lose ALL persisted data on restart. The app fails to start loudly.
    """
    if not settings.DATABASE_URL:
        logger.info("DATABASE_URL not set. Using in-memory store and checkpointer.")
        return InMemoryStore(), MemorySaver(), None

    from langgraph.store.postgres import PostgresStore
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = None
    try:
        # 同步连接池 — PostgresStore 需要同步连接
        # min_size=1: eagerly connect on startup.
        # If the database is unreachable the app will fail to start — this is
        # intentional so the operator notices immediately.
        pool = ConnectionPool(
            settings.DATABASE_URL,
            min_size=1,
            max_size=5,  # Supabase 免费版限制并发数，不宜过大
            kwargs={
                "autocommit": True,
                "prepare_threshold": None,
                "row_factory": dict_row,
            },
            # 每次借出连接时 ping 验证存活，死了自动重建 — 防止 Supabase
            # 关闭空闲连接后，池子把死连接借出去导致
            # "server closed the connection unexpectedly"
            check=ConnectionPool.check_connection,
            # 连接生命周期管理：定期轮换 + 回收空闲，降低被服务器掐断的概率
            max_lifetime=1800,   # 30 min 强制重建连接
            max_idle=600,        # 10 min 空闲回收
            reconnect_timeout=10,
        )
        store = PostgresStore(conn=pool)
        store.setup()  # 幂等：自动建表 → 任务/画像/指令持久化到 Supabase

        # 验证 store 读写正常
        try:
            health_ns = ("_health",)
            store.put(health_ns, "ping", {"ok": True})
            assert store.get(health_ns, "ping") is not None, "PostgresStore read-back failed"
            store.delete(health_ns, "ping")
            logger.info("PostgresStore verified — read/write OK.")
        except Exception as e:
            logger.critical("PostgresStore health-check failed: %s", e)
            raise _FatalStoreError(str(e)) from e

        logger.info("Using PostgresStore (Supabase) for persistence.")
        # checkpointer 暂用内存 — 业务数据 (task/profile/instructions) 走 store，已持久化
        logger.info("Using MemorySaver for checkpoints (conversation state).")
        return store, MemorySaver(), pool
    except _FatalStoreError:
        # Store is connected but broken — do NOT silently fall back to memory
        # (that would lose all persisted data on restart). Fail startup loudly.
        if pool is not None:
            try:
                pool.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        raise
    except Exception as e:
        # Couldn't even connect to the database — fall back to in-memory so
        # local dev / cold start keeps working (the operator sees the warning).
        if pool is not None:
            try:
                pool.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        logger.warning(
            "Failed to connect to Supabase PostgreSQL: %s. Falling back to in-memory store.", e
        )
        return InMemoryStore(), MemorySaver(), None


def build_graph(*, store, checkpointer, pipeline=None):
    """Compile the agent graph bound to the given store + checkpointer.

    Factored as a pure function (previously it read module globals) so the
    graph can be compiled once at startup by the container and is trivially
    recompilable in tests with an in-memory store.

    ``pipeline`` — optional ``Pipeline`` for the agent node. ``None`` uses the
    shared lazy singleton (production default); passing one lets the container
    or tests inject a custom pipeline.

    The graph includes:
    - START → route_start (conditional): routes to transcription if audio present
    - transcription subgraph: converts audio to text via Groq Whisper
    - agent: main LLM agent
    - tools: tool execution node
    """
    b = StateGraph(AgentState, context_schema=Configuration)
    b.add_node("transcription", transcription_subgraph)
    b.add_node("agent", make_agent_node(pipeline))
    # Per-user-scoped ToolNode: holds ONLY the shared core tools; DingTalk MCP
    # tools are resolved per user at invocation (see scoped_tool_node.py).
    b.add_node("tools", ScopedToolNode(ALL_TOOLS))
    b.add_node("hitl_node", hitl_node)

    # Conditional start routing: transcription if audio present, else directly to agent
    b.add_conditional_edges(START, route_start)

    # After transcription, always go to agent
    b.add_edge("transcription", "agent")

    # Agent routing: tools, hitl_node (if pending proposals from previous round), or end
    b.add_conditional_edges("agent", route_after_agent)

    # After tools, route to hitl_node if update_tasks returned proposals, else back to agent
    b.add_conditional_edges("tools", route_after_tools)

    # After hitl_node: route back to agent so the LLM can acknowledge the
    # update.  hitl_node removes the old update_tasks tool_call messages
    # via RemoveMessage, so the agent won't re-invoke update_tasks.
    b.add_edge("hitl_node", "agent")

    return b.compile(store=store, checkpointer=checkpointer)


def save_graph_diagram(graph) -> None:
    """Draw and save the graph structure diagram (debug aid).

    容器/生产环境跳过：draw_mermaid_png 需要外部渲染，冷启动时会拖慢 5~15s。
    本地调试想看结构图时保留（SKIP_GRAPH_PNG 未设置）。
    """
    if os.environ.get("SKIP_GRAPH_PNG") == "1":
        return
    try:
        png_bytes = graph.get_graph(xray=1).draw_mermaid_png()
        with open("graph.png", "wb") as f:
            f.write(png_bytes)
        logger.info("Graph diagram saved to graph.png")
    except Exception as e:
        logger.warning("Failed to draw graph diagram: %s", e)
