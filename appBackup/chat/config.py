"""聊天域配置"""
from pydantic_settings import BaseSettings


class ChatConfig(BaseSettings):
    SYSTEM_PROMPT: str = (
        "你是一个智能任务管理助手。用户可以通过自然语言与你交流，"
        "你可以帮助他们创建清单、指派任务、查询任务状态等。"
        "请用简洁专业的中文回复。"
    )
    MAX_HISTORY_LENGTH: int = 20

    model_config = {"env_prefix": "CHAT_"}


chat_settings = ChatConfig()
