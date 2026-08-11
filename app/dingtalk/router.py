"""Per-user DingTalk MCP runtime toggle + OAuth 授权 endpoints.

DingTalk MCP is excluded from app startup to keep cold start fast. These
endpoints let ANY authenticated user load or unload their OWN DingTalk tools
at runtime without restarting the service:

    GET  /api/v1/dingtalk/status     → current per-user state
    GET  /api/v1/dingtalk/authorize  → build OAuth authorize URL (user connects their DingTalk)
    GET  /api/v1/dingtalk/callback   → DingTalk OAuth callback (exchange code → token)
    POST /api/v1/dingtalk/enable     → load THIS user's DingTalk tools + creds
    POST /api/v1/dingtalk/disable    → unload THIS user's DingTalk tools

Everything is scoped to the caller's identity (Supabase JWT via
``CurrentUserIdDep``) — enabling DingTalk for one user never affects another.
Identity is never taken from the request body.

Enable/disable are idempotent and fail-atomic (a failed enable rolls back).
The anonymous ``"default"`` identity (a shared fallback) is blocked from
enabling — it would configure tools for every anonymous visitor.
"""

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.common.dependencies import CurrentUserIdDep
from app.dingtalk.schemas import DingTalkEnableRequest
from app.dingtalk.oauth import (
    build_authorize_url,
    exchange_code_for_token,
    get_user_unionid,
    _verify_state,
)
from ainote.agents.graph import builder
from ainote.agents.memory import put_dingtalk_token
from ainote.agents.graph.dingtalk_runtime import (
    DingTalkConfigError,
    DingTalkError,
    disable_dingtalk,
    enable_dingtalk,
    mark_user_connected,
    get_status,
)

logger = logging.getLogger(__name__)

dingtalkRouter = APIRouter(prefix="/dingtalk", tags=["dingtalk"])


@dingtalkRouter.get("/status")
async def status(user_id: CurrentUserIdDep):
    """Return the current user's DingTalk MCP runtime state (read-only)."""
    return get_status(user_id)


@dingtalkRouter.get("/authorize")
async def authorize(user_id: CurrentUserIdDep):
    """生成钉钉 OAuth 授权 URL(用户连接自己的钉钉)。

    返回 ``{ authorize_url, state }``。前端将链接复制/引导用户在浏览器打开,
    用户同意后钉钉回调 ``/callback``。身份来自 ``Authorization``(JWT)。
    """
    if user_id == "default":
        raise HTTPException(status_code=400, detail="匿名用户无法连接钉钉")
    return build_authorize_url(user_id)


def _callback_page(*, ok: bool, title: str, message: str) -> HTMLResponse:
    """渲染钉钉 OAuth 回调的友好 HTML 页面(微信无法自动跳回,提示用户手动返回)。"""
    icon = "✅" if ok else "⚠️"
    color = "#16a34a" if ok else "#dc2626"
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
           background: #f5f5f5; display: flex; align-items: center; justify-content: center;
           min-height: 100vh; margin: 0; }}
    .card {{ background: #fff; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,.08);
            padding: 40px 32px; text-align: center; max-width: 340px; width: 90%; }}
    .icon {{ font-size: 56px; margin-bottom: 12px; }}
    h1 {{ font-size: 20px; color: #111; margin: 0 0 8px; }}
    p {{ font-size: 15px; color: #555; line-height: 1.6; margin: 0; }}
    .btn {{ display: inline-block; margin-top: 20px; padding: 10px 24px;
            background: {color}; color: #fff; border-radius: 999px; text-decoration: none;
            font-size: 15px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <h1>{title}</h1>
    <p>{message}</p>
    <a class="btn" href="#">返回小程序</a>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


@dingtalkRouter.get("/callback")
async def callback(
    code: str = Query(...),
    state: str = Query(...),
):
    """钉钉 OAuth 回调:校验 state → 换 token → 存 per-user store。

    钉钉授权后带 ``code``(authCode) 和 ``state`` 回调这里。state 一次性,
    校验后映射回 user_id(不能从请求体拿身份)。换到用户 access_token 后
    存到该用户的 store,前端轮询 ``/status`` 刷新为 enabled。
    """
    try:
        user_id = _verify_state(state)  # 校验并消费 state → 绑定用户
        token = await exchange_code_for_token(code)
    except HTTPException as exc:
        logger.warning("dingtalk callback failed: %s", exc.detail)
        return _callback_page(
            ok=False,
            title="连接失败",
            message="授权已过期或凭证无效，请返回小程序后重新连接。",
        )
    # 用户标识:先尝试用 access_token 拿 union_id(建待办时需要);
    # 失败不阻塞存储(至少 token 已拿到,后续可再补)。
    try:
        union_id = await get_user_unionid(token["access_token"])
    except HTTPException:
        union_id = ""
    put_dingtalk_token(
        builder.store,
        user_id,
        {
            "access_token": token.get("access_token"),
            "refresh_token": token.get("refresh_token"),
            "expire_in": token.get("expire_in"),
            "union_id": union_id,
            "scope": token.get("scope"),
        },
    )
    # 同步内存注册表 + store 的 enabled=True,让 GET /status 返回 enabled=true。
    # (get_status 优先读内存 _user_runtimes 的 rt.enabled,disable 会把它置 False,
    #  回调必须同步内存,否则 disable 后再 connect 会不一致。)
    await mark_user_connected(user_id)
    logger.info("dingtalk oauth callback ok user=%s union=%s", user_id, union_id or "?")
    return _callback_page(
        ok=True,
        title="钉钉连接成功",
        message="你的钉钉账号已成功连接，现在可以返回小程序使用了。",
    )


@dingtalkRouter.post("/enable")
async def enable(
    user_id: CurrentUserIdDep,
    body: DingTalkEnableRequest | None = None,
):
    """Load THIS user's DingTalk MCP tools with their own credentials.

    Body optional: with ``credentials`` they are upserted (merged over any
    previously stored ones) before loading; without a body, previously stored
    credentials are reused. Idempotent; ``DingTalkError`` → 502.
    """
    if user_id == "default":
        raise HTTPException(
            status_code=400,
            detail="Anonymous users cannot enable DingTalk",
        )
    credentials = None
    if body is not None and body.credentials is not None:
        credentials = body.credentials.model_dump(exclude_none=True)
    try:
        return await enable_dingtalk(user_id, credentials)
    except DingTalkConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DingTalkError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@dingtalkRouter.post("/disable")
async def disable(user_id: CurrentUserIdDep):
    """Unload THIS user's DingTalk MCP tools. Idempotent.

    Credentials are kept so re-enabling does not require re-entering them.
    """
    return await disable_dingtalk(user_id)
