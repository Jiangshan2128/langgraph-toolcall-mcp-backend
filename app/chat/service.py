import json
import logging

from langchain_core.messages import AIMessageChunk, HumanMessage, AIMessage

from app.agents.config import Configuration
from app.core.thread import resolve_thread_id
from app.graph import builder
from app.graph.state import AgentState
from app.store.memory import get_tasks

logger = logging.getLogger(__name__)


async def chat_llm(message: str, user_id: str = "default", audio_bytes: bytes | None = None, audio_filename: str | None = None, audio_language: str | None = None) -> dict:
    """Invoke the LangGraph agent and return reply + tasks.
    
    Args:
        message: User text message
        user_id: User identifier
        audio_bytes: Audio file bytes (optional)
        audio_filename: Original audio filename (optional)
        audio_language: Audio language hint (optional)
    """
    logger.info("chat_llm called user=%s has_audio=%s", user_id, audio_bytes is not None)
    
    thread_id = resolve_thread_id(user_id)
    config = {"configurable": {"thread_id": thread_id}}
    
    # Build state with optional audio data
    state: AgentState = {
        "messages": [HumanMessage(content=message)] if message else [],
        "user_id": user_id,
        "audio_bytes": audio_bytes,
        "audio_filename": audio_filename,
        "audio_language": audio_language,
    }

    print(f"state message: {state['messages']}")
    
    # if audio_bytes:
    #     state["audio_bytes"] = audio_bytes
    #     state["audio_filename"] = audio_filename
    #     state["audio_language"] = audio_language
    
    result = await builder.graph.ainvoke(
        state,
        config=config,
        context=Configuration(user_id=user_id),
    )
    reply = result["messages"][-1].content
    tasks = get_tasks(builder.store, user_id)
    return {"reply": reply, "tasks": tasks}


TYPING_DELAY = 0.03  # 每个 token 之间的延迟（秒），0 = 无延迟，0.03 ≈ 打字机感（暂未启用）


async def chat_llm_stream(message: str, user_id: str = "default", audio_bytes: bytes | None = None, audio_filename: str | None = None, audio_language: str | None = None):
    """Stream the LangGraph agent output via SSE."""
    streamed_text = ""
    thread_id = resolve_thread_id(user_id)
    config = {"configurable": {"thread_id": thread_id}}

    # 立即推送连接确认，防止前端/代理因长时间无数据而超时
    yield {"event": "connected", "data": ""}
    logger.info("chat_stream started user=%s has_audio=%s", user_id, audio_bytes is not None)
    # Build state with optional audio data
    state: AgentState = {
        "messages": [HumanMessage(content=message)] if message else [],
        "user_id": user_id,
    }
    
    if audio_bytes:
        state["audio_bytes"] = audio_bytes
        state["audio_filename"] = audio_filename
        state["audio_language"] = audio_language

    try:
        async for msg, _ in builder.graph.astream(
            state,
            config=config,
            context=Configuration(user_id=user_id),
            stream_mode="messages",
        ):
            # 只处理有文本内容的 AI 消息 chunk，过滤掉 tool call 元数据等空 content
            if isinstance(msg, (AIMessageChunk, AIMessage)):
                chunk = msg.content
                if chunk:
                    streamed_text += chunk
                    logger.debug("chat_stream chunk user=%s chunk=%r", user_id, chunk)
                    yield {"event": "message", "data": chunk}
    except Exception as exc:
        logger.exception("chat_stream graph error user=%s", user_id)
        yield {"event": "error", "data": str(exc)}
        return

    # 流结束后推送完整 task 列表
    try:
        tasks = get_tasks(builder.store, user_id)
        yield {"event": "tasks", "data": json.dumps(tasks)}
    except Exception as exc:
        logger.exception("chat_stream get_tasks error user=%s", user_id)
        yield {"event": "tasks", "data": "[]"}

    yield {"event": "done", "data": ""}
    logger.info("chat_stream finished user=%s text_len=%d", user_id, len(streamed_text))