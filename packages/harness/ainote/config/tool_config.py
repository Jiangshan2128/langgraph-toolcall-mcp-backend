"""External tool integration settings.

Covers:
- Tavily web search
- Groq Whisper transcription
- DingTalk MCP (server-side, stdio via ``npx dingtalk-mcp``)
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class ToolConfig(BaseSettings):
    # Tavily web search
    TAVILY_API_KEY: str = ""

    # Groq Whisper transcription (audio → text). Free tier.
    # Get a key at https://console.groq.com/keys
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com"
    GROQ_TRANSCRIPTION_MODEL: str = "whisper-large-v3-turbo"

    # DingTalk MCP (stdio via `npx dingtalk-mcp`)
    DINGTALK_MCP_ENABLED: bool = False
    DINGTALK_CLIENT_ID: str = ""          # AppKey
    DINGTALK_CLIENT_SECRET: str = ""      # AppSecret
    DINGTALK_ACTIVE_PROFILES: str = "ALL"  # 功能模块，逗号分隔；ALL=全部
    DINGTALK_AGENT_ID: str = ""           # 可选，应用 AgentId
    ROBOT_CODE: str = ""                  # 可选，企业内部机器人编码
    ROBOT_ACCESS_TOKEN: str = ""          # 可选，自定义群机器人 webhook access_token

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


_tool_config: ToolConfig | None = None


def get_tool_config() -> ToolConfig:
    """Return the cached ``ToolConfig`` singleton."""
    global _tool_config
    if _tool_config is None:
        _tool_config = ToolConfig()
    return _tool_config
