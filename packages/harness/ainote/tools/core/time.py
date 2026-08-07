"""Time/date tools for the agent."""

from datetime import datetime

from langchain.tools import tool


@tool
def get_current_time() -> str:
    """Return the current date and time (YYYY-MM-DD HH:MM, weekday).

    Use this when you need to know today's date, the current time, or the
    weekday — e.g. to resolve relative dates like "明天"/"后天"/"这周五"
    into concrete dates for tasks.
    """
    now = datetime.now()
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return f"{now.strftime('%Y-%m-%d %H:%M')} {weekdays[now.weekday()]}"
