import uuid
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage, merge_message_runs
from langgraph.runtime import Runtime
from trustcall import create_extractor

from app.agents.config import (
    CREATE_INSTRUCTIONS,
    MODEL_SYSTEM_MESSAGE,
    TRUSTCALL_INSTRUCTION,
    Configuration,
    get_model,
)
from app.graph.state import AgentState
from app.schemas.domain import Profile, Task, UpdateMemory
from app.store.memory import (
    get_instructions,
    get_profile,
    get_tasks,
    put_instructions,
    put_profile,
    put_tasks,
)


async def main_node(
    state: AgentState,
    runtime: Runtime[Configuration],
):
    """Load memories and decide whether to update memory or respond."""
    user_id = state.get("user_id") or runtime.context.user_id

    profile = get_profile(runtime.store, user_id)
    tasks = get_tasks(runtime.store, user_id)
    instructions = get_instructions(runtime.store, user_id)

    system_msg = MODEL_SYSTEM_MESSAGE.format(
        user_profile=profile or "未设置",
        tasks="\n".join(str(task) for task in tasks) or "无",
        instructions=instructions.get("memory", "") if instructions else "无",
    )

    model = get_model()
    response = await model.bind_tools(
        [UpdateMemory],
        parallel_tool_calls=False,
    ).ainvoke([SystemMessage(content=system_msg)] + state["messages"])

    return {"messages": [response]}


async def update_profile(
    state: AgentState,
    runtime: Runtime[Configuration],
):
    """Update the user's profile memory using Trustcall."""
    user_id = state.get("user_id") or runtime.context.user_id
    namespace = ("profile", user_id)

    existing_items = runtime.store.search(namespace)
    existing_memories = (
        [(item.key, "Profile", item.value) for item in existing_items]
        if existing_items
        else None
    )

    instruction = TRUSTCALL_INSTRUCTION.format(time=datetime.now().isoformat())
    updated_messages = list(
        merge_message_runs(
            messages=[SystemMessage(content=instruction)] + state["messages"][:-1]
        )
    )

    extractor = create_extractor(
        get_model(),
        tools=[Profile],
        tool_choice="Profile",
    )
    result = await extractor.ainvoke(
        {"messages": updated_messages, "existing": existing_memories}
    )

    updates: list[tuple[str, dict]] = []
    for response, metadata in zip(result["responses"], result["response_metadata"]):
        key = metadata.get("json_doc_id", str(uuid.uuid4()))
        updates.append((key, response.model_dump(mode="json")))
    put_profile(runtime.store, user_id, updates[0][1], key=updates[0][0])

    tool_calls = state["messages"][-1].tool_calls
    return {
        "messages": [
            {
                "role": "tool",
                "content": "updated profile",
                "tool_call_id": tool_calls[0]["id"],
            }
        ]
    }


def _create_extractor_update_node(
    schema: type,
    namespace_prefix: str,
    tool_name: str,
    *,
    enable_inserts: bool = False,
):
    """Factory for structured-memory worker nodes (tasks, profile)."""
    extractor = create_extractor(
        get_model(),
        tools=[schema],
        tool_choice=tool_name,
        enable_inserts=enable_inserts,
    )

    async def node(
        state: AgentState,
        runtime: Runtime[Configuration],
    ):
        user_id = state.get("user_id") or runtime.context.user_id
        namespace = (namespace_prefix, user_id)

        existing_items = runtime.store.search(namespace)
        existing_memories = (
            [(item.key, tool_name, item.value) for item in existing_items]
            if existing_items
            else None
        )

        instruction = TRUSTCALL_INSTRUCTION.format(time=datetime.now().isoformat())
        updated_messages = list(
            merge_message_runs(
                messages=[SystemMessage(content=instruction)] + state["messages"][:-1]
            )
        )

        result = await extractor.ainvoke(
            {"messages": updated_messages, "existing": existing_memories}
        )

        updates: list[tuple[str, dict]] = []
        for response, metadata in zip(result["responses"], result["response_metadata"]):
            key = metadata.get("json_doc_id", str(uuid.uuid4()))
            updates.append((key, response.model_dump(mode="json")))

        put_tasks(runtime.store, user_id, updates)

        tool_calls = state["messages"][-1].tool_calls
        return {
            "messages": [
                {
                    "role": "tool",
                    "content": f"updated {namespace_prefix}",
                    "tool_call_id": tool_calls[0]["id"],
                }
            ]
        }

    return node


update_tasks = _create_extractor_update_node(
    Task,
    namespace_prefix="task",
    tool_name="Task",
    enable_inserts=True,
)


async def update_instructions(
    state: AgentState,
    runtime: Runtime[Configuration],
):
    """Update user-specified planning instructions."""
    user_id = state.get("user_id") or runtime.context.user_id

    existing = get_instructions(runtime.store, user_id)
    current = existing.get("memory") if existing else None

    system_msg = CREATE_INSTRUCTIONS.format(current_instructions=current or "无")
    model = get_model()
    new_memory = await model.ainvoke(
        [SystemMessage(content=system_msg)]
        + state["messages"][:-1]
        + [HumanMessage(content="Please update the instructions based on the conversation")]
    )

    put_instructions(runtime.store, user_id, {"memory": new_memory.content})

    tool_calls = state["messages"][-1].tool_calls
    return {
        "messages": [
            {
                "role": "tool",
                "content": "updated instructions",
                "tool_call_id": tool_calls[0]["id"],
            }
        ]
    }
