import logging

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.memory import InMemoryStore

from app.agents.config import Configuration
from app.core.config import settings
from app.graph.nodes import agent_node
from app.graph.routing import route_after_agent, route_start
from app.graph.state import AgentState
from app.tools import ALL_TOOLS
from app.transcription.graph import transcription_subgraph

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
    
    # Conditional start routing: transcription if audio present, else directly to agent
    b.add_conditional_edges(START, route_start)
    
    # After transcription, always go to agent
    b.add_edge("transcription", "agent")
    
    # Agent routing: tools or end
    b.add_conditional_edges("agent", route_after_agent)
    
    # After tools, return to agent for summarization
    b.add_edge("tools", "agent")
    
    return b.compile(store=store, checkpointer=checkpointer)


# Core graph (built at import with the static tool set). If DingTalk MCP is
# enabled, `init_graph()` (called from the FastAPI lifespan) extends ALL_TOOLS
# and reassigns `graph`. Consumers should access `builder.graph` (module attr),
# not bind the value at import, to see the post-startup instance.
graph = build_graph()


async def init_graph():
    """Load DingTalk MCP tools and rebuild the graph with the full tool set.

    Called once during app startup. Failures are logged and non-fatal: the core
    graph stays in place.
    """
    global graph
    from app.tools.dingtalk import load_dingtalk_tools

    dt_tools = await load_dingtalk_tools()
    if not dt_tools:
        return

    existing = {t.name for t in ALL_TOOLS}
    added = 0
    for t in dt_tools:
        if t.name not in existing:
            ALL_TOOLS.append(t)
            existing.add(t.name)
            added += 1

    if added:
        graph = build_graph()
        logger.info(
            "Graph rebuilt with DingTalk MCP tools (added=%d, total=%d).",
            added,
            len(ALL_TOOLS),
        )


# 绘制并保存 graph 结构图
try:
    png_bytes = graph.get_graph(xray=1).draw_mermaid_png()
    output_path = "graph.png"
    with open(output_path, "wb") as f:
        f.write(png_bytes)
    logger.info("Graph diagram saved to %s", output_path)
except Exception as e:
    logger.warning("Failed to draw graph diagram: %s", e)