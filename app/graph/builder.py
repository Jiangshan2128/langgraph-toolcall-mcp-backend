import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
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

        pool = ConnectionPool(
            settings.DATABASE_URL,
            min_size=1,
            max_size=5,  # Supabase 免费版限制并发数，不宜过大
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        store = PostgresStore(conn=pool)
        store.setup()  # 幂等：自动建表
        checkpointer = AsyncPostgresSaver(conn=pool)
        checkpointer.setup()  # 幂等：自动建 checkpoint 表
        logger.info("Using PostgresStore (Supabase) for persistence.")
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
