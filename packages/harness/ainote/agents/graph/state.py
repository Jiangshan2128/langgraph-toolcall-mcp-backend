from typing import Annotated, Literal

from langgraph.graph import MessagesState


def _reduce_promoted(
    current: list[str] | None,
    update: list[str] | None,
) -> list[str]:
    """Accumulate promoted tool names, deduplicating by name."""
    if update is None:
        return current or []
    if current is None:
        return update
    merged = list(current)
    for name in update:
        if name not in merged:
            merged.append(name)
    return merged


class AgentState(MessagesState, total=False):
    """Graph state for the Banana Todo List agent.

    In addition to the message list, the state carries runtime context that
    should be checkpointed together with the conversation.
    """

    user_id: str
    update_type: Literal["task", "profile", "instructions"]
    audio_bytes: bytes | None = None
    audio_filename: str | None = None
    audio_language: str | None = None
    promoted_tools: Annotated[list[str], _reduce_promoted]  # DingTalk MCP tools promoted via tool_search