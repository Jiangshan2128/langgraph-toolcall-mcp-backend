from langchain_core.messages import HumanMessage

from app.agents.config import Configuration
from app.graph.builder import graph, store
from app.store.memory import get_tasks


async def chat_llm(message: str, user_id: str = "default") -> dict:
    """Invoke the LangGraph agent and return reply + tasks."""
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=message)]},
        context=Configuration(user_id=user_id),
    )
    reply = result["messages"][-1].content
    tasks = get_tasks(store, user_id)
    return {"reply": reply, "tasks": tasks}
