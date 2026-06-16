import logging
import os

from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI

from app.chat.router import chatRouter
from app.core.config import settings

fastApi = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
)

fastApi.include_router(chatRouter, prefix="/api/v1")

@fastApi.get("/")
async def root():
    return {"message": "Hi AI Note Backend is running", "version": settings.APP_VERSION}