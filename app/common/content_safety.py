"""微信内容安全校验（security.msgSecCheck v2）。

对用户发送到对话的文本做合规检查，命中风险内容（政治敏感/违法/色情/
广告/辱骂/低俗等）时拒绝进入 LLM 流程。微信审核要求含 UGC / AI 生成
内容的类目接入内容安全。

调用链：`POST /chat/jobs` 的 `runner.submit()` → `check_text_safety()`。
风险文本返回 87014，直接拒绝；其余错误（appid 未配置、网络失败、频率
限制）按「保守放行」处理 —— 校验服务本身不可用不应卡死用户对话，但会
记录告警。身份配置复用 ``WECHAT_APPID`` / ``WECHAT_APPSECRET``。

参考：https://developers.weixin.qq.com/miniprogram/dev/OpenApiDoc/sec-center/sec-check/msgSecCheck.html
"""

from __future__ import annotations

import logging

import httpx

from ainote.config.auth_config import get_auth_config
from app.common.wechat_ssl import wechat_ssl_context

logger = logging.getLogger(__name__)

WECHAT_MSG_SEC_CHECK_URL = "https://api.weixin.qq.com/wxa/msg_sec_check"

# msgSecCheck 错误码:87014 命中风险内容。
ERRCODE_RISK = 87014
# 需要换 access_token 重试的错误码。
ERRCODE_NEED_TOKEN = (40001, 42001, 40014)
# 正常但需重试的临时错误。
_ERRCODE_RETRYABLE = (45009, 41001)
_MAX_RETRIES = 2

_cache: dict[str, str | None] = {}
_token_lock = False


def _access_token(cfg) -> str | None:
    """Cached WeChat global access_token (appid:secret → token).

    Returns ``None`` when WeChat isn't configured (no appid/secret) — callers
    treat that as "no content check available" (skip).
    """
    if not (cfg.WECHAT_APPID and cfg.WECHAT_APPSECRET):
        return None
    key = f"{cfg.WECHAT_APPID}:{cfg.WECHAT_APPSECRET}"
    token = _cache.get(key)
    if token:
        return token
    url = (
        "https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={cfg.WECHAT_APPID}"
        f"&secret={cfg.WECHAT_APPSECRET}"
    )
    try:
        # 同一 api.weixin.qq.com,容器内网关自签 CA 需用系统信任库(见
        # app.common.wechat_ssl);否则这里会抛 CERT_VERIFY_FAILED。
        with httpx.Client(timeout=10, verify=wechat_ssl_context()) as client:
            data = client.get(url).json()
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("weixin access_token fetch failed: %s", exc)
        return None
    token = data.get("access_token")
    if not token:
        logger.warning("weixin access_token error: %s", data.get("errmsg", data))
        return None
    _cache[key] = token
    return token


def _clear_token(cfg) -> None:
    """Drop a cached token after WeChat reports it invalid (40001/42001)."""
    key = f"{cfg.WECHAT_APPID}:{cfg.WECHAT_APPSECRET}"
    _cache.pop(key, None)


def _msg_sec_check(cfg, token: str, content: str, version: int, scene: int) -> dict:
    """Call ``/wxa/msg_sec_check`` once with the given token/version/scene."""
    try:
        with httpx.Client(timeout=10, verify=wechat_ssl_context()) as client:
            resp = client.post(
                WECHAT_MSG_SEC_CHECK_URL,
                params={"access_token": token},
                json={"version": version, "scene": scene, "content": content},
            )
            data = resp.json()
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("weixin msg_sec_check unreachable: %s", exc)
        return {"errcode": -1, "errmsg": str(exc)}
    return data


def check_text_safety(content: str, scene: int = 2) -> bool:
    """Return ``True`` when ``content`` is safe to process.

    - Risk content (87014)           → ``False`` (reject).
    - Missing appid/secret           → ``True`` (no check available, skip).
    - Transient / token errors       → ``True`` (fail-open), one token refresh
      retry on 40001/42001/40014.

    ``scene``: 1=资料 2=评论 3=论坛 4=社交日志 — 默认 2（评论/发言）。
    """
    cfg = get_auth_config()
    token = _access_token(cfg)
    if not token:
        logger.debug("content safety skipped (WeChat not configured)")
        return True

    for attempt in range(_MAX_RETRIES + 1):
        data = _msg_sec_check(cfg, token, content, version=2, scene=scene)
        errcode = data.get("errcode")
        if errcode == 0:
            return True
        if errcode == ERRCODE_RISK:
            logger.info("msg_sec_check rejected content (87014)")
            return False
        if errcode in ERRCODE_NEED_TOKEN and attempt < _MAX_RETRIES:
            _clear_token(cfg)
            token = _access_token(cfg)
            if token:
                continue
        if errcode in _ERRCODE_RETRYABLE and attempt < _MAX_RETRIES:
            continue
        # 其他错误(配置缺失 / 网络 / 频率超限):保守放行,记录告警。
        logger.warning(
            "msg_sec_check non-blocking error errcode=%s errmsg=%s — allowing",
            errcode,
            data.get("errmsg"),
        )
        return True
    return True


# AI 回复命中风险时的安全占位文案(不向用户透出原始违规内容)。
RISKY_REPLY_FALLBACK = (
    "抱歉，这条回复可能包含不当内容，已为你过滤。"
    "可以换一种说法重新描述需求。"
)


def filter_risky_reply(reply: str) -> str:
    """Filter an AI reply: pass through safe text, replace risky with a placeholder.

    与 ``check_text_safety`` 同一套 fail-open 语义:校验服务不可用(未配置/
    网络/频率超限)时按安全放行,原样返回;仅微信明确判定违规(87014)时替换。
    """
    if not reply or not reply.strip():
        return reply
    return reply if check_text_safety(reply) else RISKY_REPLY_FALLBACK
