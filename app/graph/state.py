from typing import Literal

from langgraph.graph import MessagesState


class AgentState(MessagesState, total=False):
    """Graph state for the AI Note agent.

    In addition to the message list, the state carries runtime context that
    should be checkpointed together with the conversation.
    """

    user_id: str
    update_type: Literal["task", "profile", "instructions"]
