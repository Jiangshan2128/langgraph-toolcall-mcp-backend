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

    # Database (Supabase / Local PostgreSQL)
    DATABASE_URL: str | None = None

    # Tavily web search
    TAVILY_API_KEY: str = ""

    # Groq Whisper transcription (audio → text). Free tier.
    # Get a key at https://console.groq.com/keys
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com"
    GROQ_TRANSCRIPTION_MODEL: str = "whisper-large-v3-turbo"

    # 钉钉服务端 MCP (stdio via `npx dingtalk-mcp`)
    DINGTALK_MCP_ENABLED: bool = False
    DINGTALK_CLIENT_ID: str = ""          # AppKey
    DINGTALK_CLIENT_SECRET: str = ""      # AppSecret
    DINGTALK_ACTIVE_PROFILES: str = "ALL"  # 功能模块，逗号分隔；ALL=全部
    DINGTALK_AGENT_ID: str = ""           # 可选，应用 AgentId
    ROBOT_CODE: str = ""                  # 可选，企业内部机器人编码
    ROBOT_ACCESS_TOKEN: str = ""          # 可选，自定义群机器人 webhook access_token

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()