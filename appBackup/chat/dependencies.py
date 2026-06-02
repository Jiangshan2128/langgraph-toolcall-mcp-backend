"""聊天域依赖注入"""
from appBackup.chat.service import chat_chain


def get_chat_chain():
    return chat_chain
