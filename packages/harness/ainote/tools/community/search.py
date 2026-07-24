from langchain.tools import tool
from tavily import TavilyClient

from ainote.config.settings import settings


@tool
def web_search(query: str) -> str:
    """Search the web for up-to-date information.

    Use this when the user asks about current events, facts, or anything
    that requires information beyond the conversation history.
    """
    if not settings.TAVILY_API_KEY:
        return "TAVILY_API_KEY is not configured, cannot search the web."

    client = TavilyClient(api_key=settings.TAVILY_API_KEY)
    response = client.search(query=query, max_results=3)
    results = response.get("results", [])
    if not results:
        return "No relevant web results found."

    snippets = []
    for r in results:
        title = r.get("title", "")
        content = r.get("content", "")
        url = r.get("url", "")
        snippets.append(f"{title}\n{content}\nSource: {url}")

    return "\n\n---\n\n".join(snippets)
