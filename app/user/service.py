"""User profile REST service layer.

``PUT /user/profile`` 由前端调用，携带原始 JSON 字符串，替换当前用户的
profile 文档。JSON 用 agent 侧的 ``Profile`` schema（
``ainote.tools.core.memory.Profile``）校验后写入 store，因为：

1. Store 是 LLM 工具与 REST API 之间唯一的共享契约 —— 两者写的是同一个
   命名空间 ``("profile", user_id)``。复用 ``Profile`` 保证 LLM 侧
   ``update_profile`` 工具和这里产出的文档形状完全一致，不会漂移。
2. 非法输入（坏 JSON、类型错误）在入库前被拒绝（422），而不是污染 store。

复用 agent 侧模型是现有架构惯例：``app/chat/schemas.py`` 的 ``TaskOut``
已经继承了 agent 侧的 ``Task`` 模型。
"""

import json
import logging

from pydantic import ValidationError

from ainote.agents.memory import delete_all_user_data, get_profile, put_profile
from ainote.config.auth_config import get_auth_config
from ainote.tools.core.memory import Profile
from app.auth.wechat_service import _gotrue_admin_delete

logger = logging.getLogger(__name__)


class ProfileValidationError(ValueError):
    """Raised when frontend-supplied profile JSON is not a valid ``Profile``."""


def update_user_profile(store, user_id: str, raw_json: str) -> dict:
    """Validate ``raw_json`` against ``Profile`` and replace the user's profile in the store.

    PUT 语义：整份文档整体替换（未提供的字段落为 ``None``），与
    ``update_profile`` 工具的整份写入行为一致。

    ``store`` is injected by the router (``Depends``) — never a module global.

    Returns the persisted profile as a JSON-safe dict.
    """
    try:
        profile = Profile.model_validate_json(raw_json)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise ProfileValidationError(str(exc)) from exc

    data = profile.model_dump(mode="json")
    put_profile(store, user_id, data)
    logger.info("Profile replaced for user=%s", user_id)
    return data


def get_user_profile(store, user_id: str) -> dict | None:
    """Return the current profile for a user, or ``None`` if not set.

    Reads the same ``("profile", user_id)`` namespace the LLM's
    ``update_profile`` tool writes, so the REST GET and the agent share one
    source of truth. The frontend calls this after login to hydrate its
    profile UI.
    """
    return get_profile(store, user_id)


async def delete_user_account(store, user_id: str) -> dict:
    """Delete a user's account: local store data + (best-effort) the GoTrue user.

    Called by ``DELETE /api/v1/user/account`` after the frontend confirmation.
    1. Remove every memory namespace the user owns (tasks / profile /
       instructions) via ``delete_all_user_data``.
    2. Delete the Supabase (GoTrue) user so they can't sign back in. Best
       effort: missing service_role, network failure, or 404 (already gone)
       do NOT fail the request — the store data is already gone and the
       access token expires on its own.

    Returns ``{"ok": True, "deleted": n}``.
    """
    deleted = delete_all_user_data(store, user_id)
    await _gotrue_admin_delete(get_auth_config(), user_id)
    logger.info("Account deleted for user=%s (%d store items removed)", user_id, deleted)
    return {"ok": True, "deleted": deleted}
