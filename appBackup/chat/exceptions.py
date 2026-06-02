"""聊天域异常"""
from fastapi import HTTPException, status


class ChatException(HTTPException):
    def __init__(self, detail: str = "Chat service error"):
        super().__init__(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
