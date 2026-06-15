import logging

from langgraph.graph import START, END, MessagesState, StateGraph
from langgraph.store.memory import InMemoryStore

from app.agents.config import Configuration
from app.core.config import settings
from app.graph.nodes import (
    main_node,
    update_instructions,
    update_profile,
    update_tasks,
)
from app.graph.routing import route_message

logger = logging.getLogger(__name__)

store = None
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
        logger.info("Using PostgresStore (Supabase) for persistence.")
    except Exception as e:
        logger.warning(
            "Failed to connect to Supabase PostgreSQL: %s. Falling back to InMemoryStore.", e
        )
        store = InMemoryStore()
else:
    store = InMemoryStore()
    logger.info("DATABASE_URL not set. Using InMemoryStore.")

builder = StateGraph(MessagesState, context_schema=Configuration)

builder.add_node("main_node", main_node)
builder.add_node("update_tasks", update_tasks)
builder.add_node("update_profile", update_profile)
builder.add_node("update_instructions", update_instructions)

builder.add_edge(START, "main_node")
builder.add_conditional_edges("main_node", route_message)
builder.add_edge("update_tasks", "main_node")
builder.add_edge("update_profile", "main_node")
builder.add_edge("update_instructions", "main_node")

# TODO: add inMemorySaver checkpointer
graph = builder.compile(store=store)
