"""MCP tools — search and retrieval.

Tools registered at import time via ``@mcp.tool``:

    search_docs      — Semantic search for relevant documentation
    get_document     — Read a full document by path
    list_docs        — List all indexed documents
"""

from __future__ import annotations

import logging
from pathlib import Path

from rag_kb.tools import get_retriever, mcp

logger = logging.getLogger(__name__)


@mcp.tool
def search_docs(query: str, top_k: int = 5) -> str:
    """Search the RAG knowledge base for semantically relevant information.

    Use this tool to find relevant documentation, code examples, or
    architecture notes.  Returns content chunks with source metadata and
    relevance scores.

    Args:
        query: Natural language query describing what you're looking for.
        top_k: Number of top results to return (default: 5, max: 20).

    Returns:
        Formatted search results with content, source, and relevance score.
    """
    top_k = min(top_k, 20)
    retriever = get_retriever()
    return retriever.search(query, top_k=top_k)


@mcp.tool
def get_document(path: str) -> str:
    """Read a full document from the knowledge base by its relative path.

    Use this when you need the complete content of a document, not just
    the snippet returned by ``search_docs``.

    Args:
        path: Relative path within the documents directory
              (e.g. ``api/auth.md``).

    Returns:
        The full text content of the document, or an error message
        if not found.
    """
    from rag_kb.config import get_rag_config

    config = get_rag_config()
    doc_dir = Path(config.DOCUMENTS_PATH)
    full_path = (doc_dir / path).resolve()

    # Security: prevent path traversal outside documents directory
    if not str(full_path).startswith(str(doc_dir.resolve())):
        return (
            f"Error: path traversal detected — '{path}' is outside "
            "the documents directory."
        )

    if not full_path.is_file():
        return f"Error: document not found at '{path}'."

    try:
        text = full_path.read_text(encoding="utf-8")
        suffix = full_path.suffix.lower()
        lines = [
            f"# {path}",
            f"```{suffix.lstrip('.')}",
            text.rstrip(),
            "```",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading document '{path}': {e}"


@mcp.tool
def list_docs() -> str:
    """List all indexed documents in the knowledge base.

    Returns each document with its chunk count, file name, and
    source path.

    Returns:
        Formatted list of indexed documents.
    """
    retriever = get_retriever()
    return retriever.list_sources()
