from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from app.agents.config import (
    MODEL_SYSTEM_MESSAGE,
    Configuration,
    get_model,
)
from app.graph.state import AgentState
from app.store.memory import (
    get_instructions,
    get_profile,
    get_tasks,
)
from app.tools import ALL_TOOLS


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

    model = get_model().bind_tools(ALL_TOOLS)
    response = await model.ainvoke(
        [SystemMessage(content=system_msg)] + state["messages"]
    )
    return {"messages": [response]}

