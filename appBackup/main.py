"""FastAPI 应用入口"""
from fastapi import FastAPI

from appBackup.chat.router import router as chat_router
from appBackup.task.router import router as task_router
from appBackup.core.config import settings

app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
)

# 挂载各业务域路由
app.include_router(chat_router, prefix="/api/v1")
app.include_router(task_router, prefix="/api/v1")


@app.get("/")
async def root():
    """
    健康检查端点，返回服务运行状态和版本信息
    
    Returns:
        dict: 包含服务状态消息和当前应用版本号的字典
    """
    return {"message": "AI Note Backend is running", "version": settings.APP_VERSION}
