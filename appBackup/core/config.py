"""全局配置"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # DeepSeek
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"

    # GLM
    GLM_API_KEY: str = ""
    GLM_MODEL: str = "glm-4.5-air"
    GLM_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4/"

    # App
    APP_TITLE: str = "AI Note Backend"
    APP_VERSION: str = "0.1.0"

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
