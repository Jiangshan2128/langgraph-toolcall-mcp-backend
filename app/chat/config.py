
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """聊天域相关的配置"""
    SYSTEM_PROMPT: str = "你是一个资深的任务规划师，擅长将复杂的任务分解成简单的步骤，并且能够根据用户的需求提供清晰、详细的指导。"

chat_settings = Settings()

    