"""User profile REST endpoints.

``userRouter`` — ``PUT /api/v1/user/profile``：前端发送原始 JSON 字符串
（期望的 profile 文档），后端用 agent 侧的 ``Profile`` schema 校验后写入
store。身份来自 ``Authorization: Bearer <Supabase access_token>``，绝不由
请求体决定。
"""

from fastapi import APIRouter, HTTPException, Request

from app.common.dependencies import CurrentUserIdDep
from app.user.service import (
    ProfileValidationError,
    get_user_profile,
    update_user_profile,
)

userRouter = APIRouter(prefix="/user", tags=["user"])


@userRouter.get("/profile")
async def get_profile_endpoint(user_id: CurrentUserIdDep) -> dict:
    """获取当前用户的档案信息。

    前端登录成功后调用，用返回的 profile 初始化用户界面。未设置档案时
    返回 ``{"ok": true, "profile": null}``。
    """
    profile = get_user_profile(user_id=user_id)
    return {"ok": True, "profile": profile}


@userRouter.put("/profile")
async def update_profile_endpoint(
    request: Request,
    user_id: CurrentUserIdDep,
) -> dict:
    """前端调用：用原始 JSON 字符串替换当前用户的档案信息。

    请求体是一个 JSON 字符串（``Content-Type: application/json``），例如::

        {"name": "张三", "gender": "男", "job": "工程师", "location": "北京"}

    后端用 ``Profile`` schema 校验后整体替换写入 store —— 与 LLM 侧
    ``update_profile`` 工具共享同一份文档形状契约。校验失败返回 422。
    """
    raw = (await request.body()).decode("utf-8")
    try:
        profile = update_user_profile(user_id=user_id, raw_json=raw)
    except ProfileValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "profile": profile}
