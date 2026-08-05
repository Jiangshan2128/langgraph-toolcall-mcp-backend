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

from ainote.agents.graph import builder
from ainote.agents.memory import put_profile
from ainote.tools.core.memory import Profile

logger = logging.getLogger(__name__)


class ProfileValidationError(ValueError):
    """Raised when frontend-supplied profile JSON is not a valid ``Profile``."""


def update_user_profile(user_id: str, raw_json: str) -> dict:
    """Validate ``raw_json`` against ``Profile`` and replace the user's profile in the store.

    PUT 语义：整份文档整体替换（未提供的字段落为 ``None``），与
    ``update_profile`` 工具的整份写入行为一致。

    Returns the persisted profile as a JSON-safe dict.
    """
    try:
        profile = Profile.model_validate_json(raw_json)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise ProfileValidationError(str(exc)) from exc

    data = profile.model_dump(mode="json")
    put_profile(builder.store, user_id, data)
    logger.info("Profile replaced for user=%s", user_id)
    return data
