import logging
import os

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.memory import InMemoryStore

from ainote.agents.models import Configuration
from ainote.config.settings import settings
from ainote.agents.graph.nodes import agent_node, hitl_node
from ainote.agents.graph.routing import route_after_agent, route_after_tools, route_start
from ainote.agents.graph.state import AgentState
from ainote.tools import ALL_TOOLS
from ainote.transcription.graph import transcription_subgraph

logger = logging.getLogger(__name__)

pool = None
store = None
checkpointer = None


class _FatalStoreError(RuntimeError):
    """Store is connected but not functional — treat as fatal (data loss risk)."""


if settings.DATABASE_URL:
    try:
        from langgraph.store.postgres import PostgresStore
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

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
            # Connected but broken store = data-loss risk. Mark fatal so the
            # outer handler re-raises instead of silently falling back to
            # in-memory (which would lose ALL persisted data on restart).
            raise _FatalStoreError(str(e)) from e

        logger.info("Using PostgresStore (Supabase) for persistence.")

        # checkpointer 暂用内存 — 业务数据 (task/profile/instructions) 走 store，已持久化
        checkpointer = MemorySaver()
        logger.info("Using MemorySaver for checkpoints (conversation state).")
    except _FatalStoreError:
        # Store is connected but broken — do NOT silently fall back to memory
        # (that would lose all persisted data on restart). Fail startup loudly.
        raise
    except Exception as e:
        logger.warning(
            "Failed to connect to Supabase PostgreSQL: %s. Falling back to in-memory store.", e
        )
        store = InMemoryStore()
        checkpointer = MemorySaver()
else:
    store = InMemoryStore()
    checkpointer = MemorySaver()
    logger.info("DATABASE_URL not set. Using in-memory store and checkpointer.")

def build_graph():
    """Compile the agent graph from the current ALL_TOOLS.

    Factored out so the graph can be recompiled after DingTalk MCP tools are
    loaded at app startup (ToolNode captures the tool list at compile time).
    
    The graph includes:
    - START → route_start (conditional): routes to transcription if audio present
    - transcription subgraph: converts audio to text via Groq Whisper
    - agent: main LLM agent
    - tools: tool execution node
    """
    b = StateGraph(AgentState, context_schema=Configuration)
    b.add_node("transcription", transcription_subgraph)
    b.add_node("agent", agent_node)
    b.add_node("tools", ToolNode(ALL_TOOLS))
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


# Core graph (built at import with the static tool set). MCP servers are NOT
# loaded at startup — they're added on demand via the toggle endpoints, which
# call rebuild_deferred_and_graph() and reassign `graph`. Consumers should
# access `builder.graph` (module attr), not bind the value at import, to see
# the post-startup instance.
graph = build_graph()


def rebuild_deferred_and_graph() -> "DeferredToolSetup | None":
    """Rebuild deferred-tool setup and recompile the graph from current ALL_TOOLS.

    Idempotent: first drops any stale ``tool_search`` from ALL_TOOLS, rebuilds
    the deferred setup from the remaining MCP tools (via
    ``build_deferred_tool_setup``), re-appends a fresh ``tool_search`` if any
    MCP tools remain, refreshes the cached setup, and recompiles ``graph``.
    Callers must have already mutated ALL_TOOLS / MCP_TOOL_NAMES.

    Returns the new ``DeferredToolSetup`` (or None when no deferred setup).
    """
    global graph
    from ainote.tools.tool_search import build_deferred_tool_setup
    from ainote.agents.graph.deferred_cache import refresh_deferred_setup

    # Drop stale tool_search so the rebuild recomputes it from remaining tools.
    ALL_TOOLS[:] = [t for t in ALL_TOOLS if t.name != "tool_search"]

    setup = build_deferred_tool_setup(ALL_TOOLS)
    if setup.tool_search_tool:
        # Add tool_search to ALL_TOOLS so ToolNode can execute it
        ALL_TOOLS.append(setup.tool_search_tool)
        logger.info("tool_search added to ALL_TOOLS (deferred=%d)", len(setup.deferred_names))
    refresh_deferred_setup(setup)
    graph = build_graph()
    logger.info("Graph rebuilt (total tools=%d)", len(ALL_TOOLS))
    return setup


# 绘制并保存 graph 结构图
# 容器/生产环境跳过：draw_mermaid_png 需要外部渲染，冷启动时会拖慢 5~15s。
# 本地调试想看结构图时保留（SKIP_GRAPH_PNG 未设置）。
if os.environ.get("SKIP_GRAPH_PNG") != "1":
    try:
        png_bytes = graph.get_graph(xray=1).draw_mermaid_png()
        output_path = "graph.png"
        with open(output_path, "wb") as f:
            f.write(png_bytes)
        logger.info("Graph diagram saved to %s", output_path)
    except Exception as e:
        logger.warning("Failed to draw graph diagram: %s", e)