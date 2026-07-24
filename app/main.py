import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from fastapi import FastAPI

from app.chat.router import chatRouter
from app.config.settings import settings
from app.agents.graph.builder import init_graph, pool
from app.chat.task_router import taskRouter

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load DingTalk MCP tools (if enabled) and rebuild the graph with the full
    # tool set. Failures are logged inside init_graph and non-fatal.
    await init_graph()
    yield
    if pool is not None:
        try:
            pool.close()
        except Exception:
            logging.getLogger(__name__).exception("Failed to close PostgreSQL connection pool")


fastApi = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

fastApi.include_router(chatRouter, prefix="/api/v1")
fastApi.include_router(taskRouter, prefix="/api/v1")

@fastApi.get("/")
async def root():
    return {"message": "Hi AI Note Backend is running", "version": settings.APP_VERSION}


@fastApi.get("/health")
async def health():
    db_backend = "postgresql" if pool is not None and not pool.closed else "memory"
    return {"status": "ok", "version": settings.APP_VERSION, "database": db_backend}