import json
import logging

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from app.agents.config import Configuration
from app.core.thread import resolve_thread_id
from app.graph import builder
from app.graph.state import AgentState
from app.store.memory import get_tasks

logger = logging.getLogger(__name__)


def _last_ai_content(messages: list) -> str:
    """Extract the last AI text reply from the message list, skipping tool calls."""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.content:
            return m.content
    return ""


# ── Non-streaming (invoke) ────────────────────────────────────────────


async def chat_llm(
    message: str = "",
    user_id: str = "default",
    audio_bytes: bytes | None = None,
    audio_filename: str | None = None,
    audio_language: str | None = None,
) -> dict:
    """Invoke the LangGraph agent and return reply + tasks.

    Uses ``ainvoke(version="v2")`` which returns a ``GraphOutput`` with
    clean ``.value`` / ``.interrupts`` attributes instead of the
    deprecated ``__interrupt__`` dict key.

    If the graph pauses on a human-in-the-loop interrupt (e.g.
    update_tasks wants to change tasks), the returned dict will carry an
    ``interrupt`` key with the approval payload.
    """
    logger.info("chat_llm called user=%s has_audio=%s", user_id, audio_bytes is not None)

    thread_id = resolve_thread_id(user_id)
    config = {"configurable": {"thread_id": thread_id}}

    state: AgentState = {
        "messages": [HumanMessage(content=message)] if message else [],
        "user_id": user_id,
        "audio_bytes": audio_bytes,
        "audio_filename": audio_filename,
        "audio_language": audio_language,
    }

    result = await builder.graph.ainvoke(
        state,
        config=config,
        context=Configuration(user_id=user_id),
        version="v2",
    )

    # ── v2: result is GraphOutput, not a dict ──
    if result.interrupts:
        interrupt_data = result.interrupts[0].value
        logger.info(
            "HITL interrupt returned user=%s type=%s",
            user_id, interrupt_data.get("type"),
        )
        reply = _last_ai_content(result.value.get("messages", []))
        tasks = get_tasks(builder.store, user_id)
        return {"reply": reply, "tasks": tasks, "interrupt": interrupt_data}

    reply = _last_ai_content(result.value["messages"])
    tasks = get_tasks(builder.store, user_id)
    return {"reply": reply, "tasks": tasks}


# ── Streaming (astream_events v3) ─────────────────────────────────────


async def chat_llm_stream(
    message: str = "",
    user_id: str = "default",
    audio_bytes: bytes | None = None,
    audio_filename: str | None = None,
    audio_language: str | None = None,
):
    """Stream the LangGraph agent output via SSE.

    Uses ``astream_events(version="v3")`` — the recommended API for
    human-in-the-loop streaming:

    - Token-by-token LLM output from ``stream.messages``
    - Clean interrupt detection via ``stream.interrupted`` /
      ``stream.interrupts`` (no ``get_state()`` hack)
    - Resume is handled by a separate ``POST /resume`` call

    When a human-in-the-loop interrupt fires inside a tool, an
    ``interrupt`` SSE event is yielded so the frontend can render an
    approval card.
    """
    thread_id = resolve_thread_id(user_id)
    config = {"configurable": {"thread_id": thread_id}}

    # 立即推送连接确认，防止前端/代理因长时间无数据而超时
    yield {"event": "connected", "data": ""}
    logger.info("chat_stream started user=%s has_audio=%s", user_id, audio_bytes is not None)

    state: AgentState = {
        "messages": [HumanMessage(content=message)] if message else [],
        "user_id": user_id,
    }

    if audio_bytes:
        state["audio_bytes"] = audio_bytes
        state["audio_filename"] = audio_filename
        state["audio_language"] = audio_language

    streamed_text = ""

    try:
        # v3: returns AsyncGraphRunStream with .messages, .interrupted, etc.
        stream = await builder.graph.astream_events(
            state,
            config=config,
            context=Configuration(user_id=user_id),
            version="v3",
        )

        # ── Stream LLM tokens from .messages ──
        async for message in stream.messages:
            async for token in message.text:
                if token:
                    streamed_text += token
                    yield {"event": "message", "data": token}

        # ── After stream finishes: check for interrupt ──
        interrupted = await stream.interrupted()
        if interrupted:
            interrupts = await stream.interrupts()
            interrupt_data = interrupts[0].value
            logger.info(
                "HITL interrupt after stream user=%s type=%s",
                user_id, interrupt_data.get("type"),
            )
            yield {
                "event": "interrupt",
                "data": json.dumps(interrupt_data, ensure_ascii=False),
            }
            yield {"event": "done", "data": ""}
            logger.info(
                "chat_stream interrupted user=%s text_len=%d",
                user_id, len(streamed_text),
            )
            return

    except Exception as exc:
        logger.exception("chat_stream graph error user=%s", user_id)
        yield {"event": "error", "data": str(exc)}
        return

    # 流结束后推送完整 task 列表
    try:
        tasks = get_tasks(builder.store, user_id)
        yield {"event": "tasks", "data": json.dumps(tasks, ensure_ascii=False)}
    except Exception as exc:
        logger.exception("chat_stream get_tasks error user=%s", user_id)
        yield {"event": "tasks", "data": "[]"}

    yield {"event": "done", "data": ""}
    logger.info("chat_stream finished user=%s text_len=%d", user_id, len(streamed_text))


# ── Resume (invoke v2) ────────────────────────────────────────────────


async def resume_graph(
    user_id: str = "default",
    decision: dict | None = None,
) -> dict:
    """Resume a paused graph with a human decision.

    Uses ``ainvoke(version="v2")`` with ``Command(resume=decision)``.
    If the graph hits another interrupt after resume (e.g. a second
    round of tool calls), it will be surfaced in the returned dict.
    """
    thread_id = resolve_thread_id(user_id)
    config = {"configurable": {"thread_id": thread_id}}

    logger.info("resume_graph user=%s decision=%s", user_id, decision)

    result = await builder.graph.ainvoke(
        Command(resume=decision or {}),
        config=config,
        context=Configuration(user_id=user_id),
        version="v2",
    )

    # v2: GraphOutput — clean .interrupts access
    if result.interrupts:
        interrupt_data = result.interrupts[0].value
        logger.info("HITL re-interrupt after resume user=%s", user_id)
        reply = _last_ai_content(result.value.get("messages", []))
        tasks = get_tasks(builder.store, user_id)
        return {"reply": reply, "tasks": tasks, "interrupt": interrupt_data}

    reply = _last_ai_content(result.value["messages"])
    tasks = get_tasks(builder.store, user_id)
    return {"reply": reply, "tasks": tasks}
