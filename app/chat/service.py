import json

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.agents.config import Configuration
from app.graph.builder import graph, store
from app.store.memory import get_tasks, delete_task as _delete_task, update_task as _update_task


async def chat_llm(message: str, user_id: str = "default") -> dict:
    """Invoke the LangGraph agent and return reply + tasks."""
    print("[chat_llm] called")
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=message)]},
        context=Configuration(user_id=user_id),
    )
    reply = result["messages"][-1].content
    tasks = get_tasks(store, user_id)
    return {"reply": reply, "tasks": tasks}


TYPING_DELAY = 0.03  # 每个 token 之间的延迟（秒），0 = 无延迟，0.03 ≈ 打字机感（暂未启用）


def delete_task(key: str, user_id: str = "default"):
    """从 store 中删除指定 task。"""
    _delete_task(store, user_id, key)


def update_task(key: str, user_id: str = "default", updates: dict = None):
    """更新 store 中指定 task 的部分字段。"""
    return _update_task(store, user_id, key, updates)


async def chat_llm_stream(message: str, user_id: str = "default"):
    """Stream the LangGraph agent output via SSE."""
    streamed_text = ""

    # 立即推送连接确认，防止前端/代理因长时间无数据而超时
    yield {"event": "connected", "data": ""}
    print(f"[chat_stream] started for user={user_id}")

    try:
        async for msg, _ in graph.astream(
            {"messages": [HumanMessage(content=message)]},
            context=Configuration(user_id=user_id),
            stream_mode="messages",
        ):
            # 只处理有文本内容的 AI 消息 chunk，过滤掉 tool call 元数据等空 content
            if isinstance(msg, (AIMessageChunk, AIMessage)):
                chunk = msg.content
                if chunk:
                    streamed_text += chunk
                    print(f"[chat_stream] chunk: {chunk!r}")
                    yield {"event": "message", "data": chunk}
    except Exception as exc:
        print(f"[chat_stream] graph error: {exc}")
        yield {"event": "error", "data": str(exc)}
        return

    # 流结束后推送完整 task 列表
    try:
        tasks = get_tasks(store, user_id)
        yield {"event": "tasks", "data": json.dumps(tasks)}
    except Exception as exc:
        print(f"[chat_stream] get_tasks error: {exc}")
        yield {"event": "tasks", "data": "[]"}

    yield {"event": "done", "data": ""}
    print(f"[chat_stream] finished for user={user_id} text_len={len(streamed_text)}")
