"""钉钉 OAuth 2.0 用户授权流程。

用户「连接自己的钉钉」——走钉钉 OAuth 授权，授权后拿到**用户个人**的
access_token / refresh_token / union_id，存到 per-user store。后端后续用
这些 token 代表用户建待办。

应用身份凭证（client_id/client_secret）来自 ``.env``（``AuthConfig``），
由运营方配置一次，不作为 per-user 凭证。

流程::

    GET  /api/v1/dingtalk/authorize
        → 生成钉钉授权 URL(带 state), 返回给前端
    用户在浏览器打开 → 钉钉授权页同意
    钉钉回调 GET /api/v1/dingtalk/callback?code=authCode&state=...
        → 校验 state → 用 code + client_id/client_secret 换 token
        → 存 per-user store
    前端轮询 /status → enabled=true
"""

from __future__ import annotations

import logging
import secrets
import urllib.parse

import httpx
from fastapi import HTTPException

from ainote.config.auth_config import get_auth_config

logger = logging.getLogger(__name__)

# 钉钉 OAuth 2.0 端点
DINGTALK_AUTHORIZE_URL = "https://login.dingtalk.com/oauth2/auth"
DINGTALK_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
DINGTALK_USERINFO_URL = "https://api.dingtalk.com/v1.0/contact/users/me"
# 拿用户 access_token 的 Header key
DINGTALK_TOKEN_HEADER = "x-acs-dingtalk-access-token"

# 内存中的 state 校验表: state -> user_id。state 一次性,回调校验后即删。
# 单 worker 部署,模块级 dict 足够;重启后未消费的 state 作废(用户重授权)。
_state_store: dict[str, str] = {}


def _new_state(user_id: str) -> str:
    """生成一次性 state 并绑定 user_id(防 CSRF)。"""
    state = secrets.token_urlsafe(24)
    _state_store[state] = user_id
    return state


def _verify_state(state: str) -> str:
    """校验并消费 state,返回绑定的 user_id;非法/已用 → 抛 400。"""
    user_id = _state_store.pop(state, None)
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state")
    return user_id


def build_authorize_url(user_id: str) -> dict:
    """为指定用户生成钉钉 OAuth 授权 URL。

    ``state`` 绑定 user_id,回调时校验。返回 ``{ authorize_url, state }``。
    """
    cfg = get_auth_config()
    if not cfg.dingtalk_oauth_enabled:
        raise HTTPException(
            status_code=503,
            detail="钉钉 OAuth 未配置(需 DINGTALK_CLIENT_ID/SECRET/REDIRECT_URI)",
        )
    state = _new_state(user_id)
    params = {
        "redirect_uri": cfg.DINGTALK_REDIRECT_URI,
        "response_type": "code",
        "client_id": cfg.DINGTALK_CLIENT_ID,
        "scope": cfg.DINGTALK_SCOPE,
        "state": state,
        "prompt": "consent",
    }
    # quote_via=quote: 空格编码为 %20 而非 +,钉钉 OAuth 要求 %20(scope=openid%20corpid)
    url = DINGTALK_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return {"authorize_url": url, "state": state}


async def exchange_code_for_token(code: str) -> dict:
    """用授权回调的 authCode 换用户 access_token。

    用应用凭证(client_id/client_secret) + authCode 调钉钉
    ``POST /v1.0/oauth2/userAccessToken``。返回钉钉的 token 响应:
    ``{ access_token, refresh_token, expire_in, ... }``。
    """
    cfg = get_auth_config()
    if not cfg.dingtalk_oauth_enabled:
        raise HTTPException(status_code=503, detail="钉钉 OAuth 未配置")

    body = {
        "clientId": cfg.DINGTALK_CLIENT_ID,
        "clientSecret": cfg.DINGTALK_CLIENT_SECRET,
        "code": code,
        "grantType": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(DINGTALK_TOKEN_URL, json=body)
            data = resp.json()
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("dingtalk token exchange unreachable: %s", exc)
        raise HTTPException(status_code=502, detail="钉钉授权服务暂不可达")

    if resp.status_code >= 400:
        logger.warning("dingtalk token exchange failed: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=502, detail="钉钉授权换取失败，请重试")
    # 钉钉返回驼峰字段(accessToken/refreshToken/expireIn),标准化为下划线,
    # 统一后续消费的字段名。
    if "accessToken" not in data and "access_token" not in data:
        logger.warning("dingtalk token exchange missing access token: %s", resp.text[:200])
        raise HTTPException(status_code=502, detail="钉钉授权换取失败，请重试")
    data["access_token"] = data.get("accessToken") or data.get("access_token")
    data["refresh_token"] = data.get("refreshToken") or data.get("refresh_token")
    if "expireIn" in data and "expire_in" not in data:
        data["expire_in"] = data["expireIn"]
    return data


async def get_user_unionid(access_token: str) -> str:
    """用用户 access_token 获取 unionId(钉钉用户唯一标识)。

    调 ``GET /v1.0/contact/users/me``,Header 带 ``x-acs-dingtalk-access-token``。
    返回 unionId;失败抛 502。
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                DINGTALK_USERINFO_URL,
                headers={DINGTALK_TOKEN_HEADER: access_token},
            )
            data = resp.json()
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("dingtalk userinfo unreachable: %s", exc)
        raise HTTPException(status_code=502, detail="获取钉钉用户信息失败")

    if resp.status_code >= 400:
        logger.warning("dingtalk userinfo failed: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=502, detail="获取钉钉用户信息失败")
    # 兼容驼峰/下划线字段名(钉钉返回 unionId)
    uid = data.get("unionId") or data.get("union_id")
    if not uid:
        logger.warning("dingtalk userinfo missing unionId: %s", resp.text[:200])
        raise HTTPException(status_code=502, detail="获取钉钉用户信息失败")
    return uid
