import logging

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime

from app.agents.config import (
    MODEL_SYSTEM_MESSAGE,
    Configuration,
)
from app.graph.state import AgentState
from app.graph.tool_router import get_model_with_tools
from app.store.memory import (
    get_instructions,
    get_profile,
    get_tasks,
)

logger = logging.getLogger(__name__)


async def agent_node(
    state: AgentState,
    runtime: Runtime[Configuration],
):
    """Load memories and decide which tools to call, if any."""
    user_id = state.get("user_id") or runtime.context.user_id

    profile = get_profile(runtime.store, user_id)
    tasks = get_tasks(runtime.store, user_id)
    instructions = get_instructions(runtime.store, user_id)

    system_msg = MODEL_SYSTEM_MESSAGE.format(
        user_profile=profile or "未设置",
        tasks="\n".join(str(task) for task in tasks) or "无",
        instructions=instructions.get("memory", "") if instructions else "无",
    )

    model = await get_model_with_tools(state["messages"])
    try:
        response = await model.ainvoke(
            [SystemMessage(content=system_msg)] + state["messages"]
        )
    except Exception as exc:
        logger.exception("agent_node model.ainvoke failed for user=%s", user_id)
        error_msg = (
            "抱歉，模型服务暂时不可用，请稍后重试。"
            if "BadRequestError" in type(exc).__name__ or "400" in str(exc)
            else f"An error occurred: {exc}"
        )
        return {"messages": [AIMessage(content=error_msg)]}

    return {"messages": [response]}