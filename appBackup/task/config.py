"""任务域配置"""
from pydantic_settings import BaseSettings


class TaskConfig(BaseSettings):
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    model_config = {"env_prefix": "TASK_"}


task_settings = TaskConfig()
