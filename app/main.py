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
from ainote.config.settings import settings
from ainote.agents.graph.builder import pool
from app.chat.task_router import taskRouter
from app.jobs.router import jobRouter
from app.user.router import userRouter
from app.auth.router import authRouter
from app.dingtalk.router import dingtalkRouter

@asynccontextmanager
async def lifespan(app: FastAPI):
    # MCP servers (DingTalk, rag) are NOT loaded at startup — they're loaded
    # on demand via the toggle endpoints (/api/v1/dingtalk/enable, etc.) to
    # keep cold start fast. The core graph is already compiled at import time
    # (builder.graph). Nothing to do here except manage the DB pool.
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


@fastApi.exception_handler(Exception)
async def _unhandled_exception_handler(request, exc):
    """Global catch-all: log the real error server-side, return a generic 500.

    Never send ``str(exc)`` to the client — it may leak DB connection strings,
    API-key fragments, or internal paths. HTTPException (4xx/5xx raised by
    routers) is handled by FastAPI's built-in handler and bypasses this.
    """
    import logging

    logging.getLogger(__name__).exception("Unhandled error on %s", request.url.path)
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=500, content={"ok": False, "error": "Internal server error"})


fastApi.include_router(chatRouter, prefix="/api/v1")
fastApi.include_router(taskRouter, prefix="/api/v1")
fastApi.include_router(jobRouter, prefix="/api/v1")
fastApi.include_router(userRouter, prefix="/api/v1")
fastApi.include_router(authRouter, prefix="/api/v1")
fastApi.include_router(dingtalkRouter, prefix="/api/v1")

@fastApi.get("/")
async def root():
    return {"message": "Hi AI Note Backend is running", "version": settings.APP_VERSION}


@fastApi.get("/health")
async def health():
    db_backend = "postgresql" if pool is not None and not pool.closed else "memory"
    return {"status": "ok", "version": settings.APP_VERSION, "database": db_backend}