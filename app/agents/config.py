from dataclasses import dataclass
from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.core.config import settings


@dataclass
class Configuration:
    """Runtime configuration for the agent graph."""

    user_id: str = "default"


@lru_cache
def get_model(temperature: float = 0.0) -> ChatOpenAI:
    """Return a configured chat model. Do not hard-code at module level."""
    return ChatOpenAI(
        model=settings.GLM_MODEL,
        api_key=settings.GLM_API_KEY,
        base_url=settings.GLM_BASE_URL,
        temperature=temperature,
    )


MODEL_SYSTEM_MESSAGE = """You are a professional task-planning assistant for AI Note.

You help users break down requests into actionable tasks and keep long-term memory about:
1. The user's profile (general information about them)
2. The user's task list
3. User-specified instructions for how tasks should be planned

Here is the current User Profile (may be empty if no information has been collected yet):
<<user_profile>
{user_profile}
</user_profile>

Here is the current Task List (may be empty if no tasks have been added yet):
<<tasks>
{tasks}
</tasks>

Here are the current user-specified preferences for updating tasks (may be empty if no preferences have been specified yet):
<<instructions>
{instructions}
</instructions>

Reason carefully about the user's messages below.

Decide whether any of your long-term memory should be updated:
- If personal information was provided about the user, update the user's profile by calling UpdateMemory with update_type `profile`.
- If tasks are mentioned, update the task list by calling UpdateMemory with update_type `task`.
- If the user has specified preferences for how tasks should be planned, update the instructions by calling UpdateMemory with update_type `instructions`.

Err on the side of updating the task list. No need to ask for explicit permission.

Respond naturally to the user after a tool call was made to save memories, or if no tool call was made."""

TRUSTCALL_INSTRUCTION = """Reflect on the following interaction.

Use the provided tools to retain any necessary memories about the user.

Use parallel tool calling to handle updates and insertions simultaneously.

When the user asks to break down a task, mentions sub-tasks, or describes a multi-step plan, you MUST return one or more Task objects using the Task tool. Each sub-task should become its own Task with:
- title: concise sub-task name
- description: brief details
- assignee: who owns it (default to the user if not specified)
- priority: P0 (urgent today), P1 (important), or P2 (routine)
- deadline: YYYY-MM-DD or descriptive text if mentioned
- pre_task: title of a prerequisite sub-task if this one depends on another
- status: "not started" by default

System Time: {time}"""

CREATE_INSTRUCTIONS = """Reflect on the following interaction.

Based on this interaction, update your instructions for how to plan and update tasks.
Use any feedback from the user to update how they like items added, prioritized, or assigned.

Your current instructions are:

<<current_instructions>
{current_instructions}
</current_instructions>"""
