import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from fastapi import FastAPI, Request

from app.chat.router import chatRouter
from ainote.config.settings import settings
from app.common.container import create_app_context
from app.chat.task_router import taskRouter
from app.jobs.router import jobRouter
from app.user.router import userRouter
from app.auth.router import authRouter
from app.dingtalk.router import dingtalkRouter
from app.diag.router import diagRouter

@asynccontextmanager
async def lifespan(app: FastAPI):
    # MCP servers (DingTalk, rag) are NOT loaded at startup — they're loaded
    # on demand via the toggle endpoints (/api/v1/dingtalk/enable, etc.) to
    # keep cold start fast. Everything long-lived (DB pool, store, graph,
    # DingTalk runtime) is built once here by the container and exposed on
    # app.state.app_context for request-scoped Depends access.
    async with create_app_context() as ctx:
        app.state.app_context = ctx
        yield


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
fastApi.include_router(diagRouter, prefix="/api/v1")

@fastApi.get("/")
async def root():
    logging.getLogger(__name__).info("root health check hit")
    return {"message": "Hi Banana Todo List Backend is running", "version": settings.APP_VERSION}


@fastApi.get("/health")
async def health(request: Request):
    """Liveness probe. Verifies the store is actually usable (not just that a
    pool object exists) so CloudBase's health check reflects real availability.
    """
    ctx = getattr(request.app.state, "app_context", None)
    store = getattr(ctx, "store", None)
    pool = getattr(ctx, "pool", None)
    db_ok = False
    if store is not None:
        try:
            ns = ("_health",)
            store.put(ns, "ping", {"ok": True})
            db_ok = store.get(ns, "ping") is not None
            store.delete(ns, "ping")
        except Exception:
            db_ok = False

    db_backend = "postgresql" if pool is not None and not pool.closed else "memory"
    return {
        "status": "ok" if db_ok else "degraded",
        "version": settings.APP_VERSION,
        "database": db_backend,
        "database_ok": db_ok,
    }