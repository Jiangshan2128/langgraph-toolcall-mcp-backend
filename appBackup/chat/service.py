"""聊天域业务逻辑 — 定义聊天专用的 Chain"""
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from appBackup.core.llm import llm
from appBackup.chat.config import chat_settings

# 聊天域专用的 prompt
chat_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", chat_settings.SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="history"),
        ("human", "{user_message}"),
    ]
)

# 聊天域专用的 Chain
chat_chain = chat_prompt | llm | StrOutputParser()


async def chat(user_message: str, history: list | None = None) -> str:
    """执行聊天"""
    msgs = history or []
    response = await chat_chain.ainvoke(
        {
            "user_message": user_message,
            "history": msgs,
        }
    )
    return response["messages"][-1].content
