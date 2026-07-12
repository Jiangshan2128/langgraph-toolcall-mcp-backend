"""MCP tool modules for the RAG knowledge base.

Tools are organized by domain and registered via ``@mcp.tool`` decorators
at import time.  ``server.py`` only needs to import the modules to
register everything.

.. code-block:: text

    rag_kb/tools/
    ├── __init__.py        — mcp instance, lifespan, retriever singleton
    ├── search.py          — search_docs, get_document, list_docs
    └── index.py           — refresh_index, get_doc_stats
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from fastmcp import FastMCP

if TYPE_CHECKING:
    from rag_kb.retriever import RAGRetriever

logger = logging.getLogger(__name__)

# ── Central FastMCP instance ─────────────────────────────────────────
# Tool modules import this and decorate via @mcp.tool.
# The lifespan ensures the retriever is initialized *before* any tool
# call arrives — no lazy-init delay on first use.


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
    """FastMCP lifespan: pre-warm retriever on startup, clean up on shutdown."""
    _init_retriever()
    yield
    reset_retriever()


mcp = FastMCP(
    "AI-Note RAG Knowledge Base",
    lifespan=_lifespan,
    instructions=(
        "This server provides a RAG (Retrieval-Augmented Generation) knowledge "
        "base query interface. Use the tools to search indexed documents and "
        "manage the knowledge base.\n\n"
        "Documents are automatically indexed on server startup from the "
        "configured documents directory. To add new documents, drop them into "
        "the documents folder and call `refresh_index` (or enable file watching)."
    ),
)

# ── Shared retriever singleton ───────────────────────────────────────
_retriever: RAGRetriever | None = None


def _init_retriever() -> None:
    """Initialize the retriever and index documents (called once via lifespan).

    Separated from ``get_retriever()`` so the lifespan can call it eagerly
    on startup, while tools still get the same singleton on demand.
    """
    global _retriever
    if _retriever is not None:
        return

    from rag_kb.config import get_rag_config
    from rag_kb.retriever import RAGRetriever

    logger.info("Startup: initializing RAGRetriever and indexing documents...")
    config = get_rag_config()
    _retriever = RAGRetriever(config)
    result = _retriever.initialize()
    logger.info("Startup indexing complete: %s", result.summary)


def get_retriever() -> RAGRetriever:
    """Return the singleton RAGRetriever.

    By the time any tool function runs, the lifespan has already called
    ``_init_retriever()`` — so this is always a fast no-op return.
    """
    global _retriever
    if _retriever is None:
        # Fallback: lifespan may not have run (e.g. in tests).
        _init_retriever()
    return _retriever


def reset_retriever() -> None:
    """Reset the retriever singleton (for shutdown / testing)."""
    global _retriever
    if _retriever is not None:
        _retriever.shutdown()
        _retriever = None
        logger.info("Retriever shut down.")
