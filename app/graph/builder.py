import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.memory import InMemoryStore

from app.agents.config import Configuration
from app.core.config import settings
from app.graph.nodes import agent_node
from app.graph.routing import route_after_agent
from app.graph.state import AgentState
from app.tools import ALL_TOOLS

logger = logging.getLogger(__name__)

pool = None
store = None
checkpointer = None

if settings.DATABASE_URL:
    try:
        from langgraph.store.postgres import PostgresStore
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        # 同步连接池 — PostgresStore 需要同步连接
        pool = ConnectionPool(
            settings.DATABASE_URL,
            min_size=1,
            max_size=5,  # Supabase 免费版限制并发数，不宜过大
            kwargs={
                "autocommit": True,
                "prepare_threshold": None,
                "row_factory": dict_row,
            },
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
            raise  # 让服务启动失败，而不是静默回退到内存

        logger.info("Using PostgresStore (Supabase) for persistence.")

        # checkpointer 暂用内存 — 业务数据 (task/profile/instructions) 走 store，已持久化
        checkpointer = MemorySaver()
        logger.info("Using MemorySaver for checkpoints (conversation state).")
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

builder = StateGraph(AgentState, context_schema=Configuration)

builder.add_node("agent", agent_node)
builder.add_node("tools", ToolNode(ALL_TOOLS))

builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", route_after_agent)
builder.add_edge("tools", "agent")

graph = builder.compile(store=store, checkpointer=checkpointer)

# 绘制并保存 graph 结构图
try:
    png_bytes = graph.get_graph(xray=1).draw_mermaid_png()
    output_path = "graph.png"
    with open(output_path, "wb") as f:
        f.write(png_bytes)
    logger.info("Graph diagram saved to %s", output_path)
except Exception as e:
    logger.warning("Failed to draw graph diagram: %s", e)
