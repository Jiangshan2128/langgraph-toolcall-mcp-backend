"""全局 LLM 基础实例初始化 — 只提供"原材料"，不定义任何 chain"""
from langchain_deepseek import ChatDeepSeek

from appBackup.core.config import settings

# 基础 LLM 实例，各业务域 import 后自行组装 chain
llm = ChatDeepSeek(
    model=settings.DEEPSEEK_MODEL,
    api_key=settings.DEEPSEEK_API_KEY,
    api_base=settings.DEEPSEEK_BASE_URL,
    temperature=0,
)
