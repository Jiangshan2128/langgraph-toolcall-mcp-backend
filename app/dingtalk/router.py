"""DingTalk MCP runtime toggle endpoints.

DingTalk MCP is excluded from app startup to keep cold start fast. These
endpoints let an authenticated operator load or unload it at runtime without
restarting the service:

    GET  /api/v1/dingtalk/status   → current state
    POST /api/v1/dingtalk/enable   → load DingTalk tools + rebuild graph
    POST /api/v1/dingtalk/disable  → unload DingTalk tools + rebuild graph

Both toggle ops are idempotent and fail-atomic (a failed enable rolls back).
"""

from fastapi import APIRouter, HTTPException

from app.common.dependencies import CurrentUserIdDep
from ainote.agents.graph.dingtalk_runtime import (
    DingTalkError,
    disable_dingtalk,
    enable_dingtalk,
    get_status,
)

dingtalkRouter = APIRouter(prefix="/dingtalk", tags=["dingtalk"])


@dingtalkRouter.get("/status")
async def status(user_id: CurrentUserIdDep):
    """Return the current DingTalk MCP runtime state."""
    return get_status()


@dingtalkRouter.post("/enable")
async def enable(user_id: CurrentUserIdDep):
    """Load DingTalk MCP tools and rebuild the graph. Idempotent."""
    try:
        return await enable_dingtalk()
    except DingTalkError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@dingtalkRouter.post("/disable")
async def disable(user_id: CurrentUserIdDep):
    """Unload DingTalk MCP tools and rebuild the graph. Idempotent."""
    return await disable_dingtalk()
