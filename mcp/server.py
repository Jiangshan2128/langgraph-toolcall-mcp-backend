"""FastMCP server -- exposes RAG knowledge base tools via MCP protocol.

Start the server::

    # Via Python (stdio transport, default):
    python mcp/server.py

    # Via FastMCP CLI (stdio):
    fastmcp run mcp/server.py:mcp

    # Via FastMCP CLI (HTTP on port 9000):
    fastmcp run mcp/server.py:mcp --transport http --port 9000

Register as a local MCP server in ``mcp_servers.json``::

    {
      "mcpServers": {
        "rag-knowledge-base": {
          "enabled": true,
          "type": "stdio",
          "command": "python",
          "args": ["mcp/server.py"],
          "description": "Local RAG knowledge base query service"
        }
      }
    }
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure the mcp/ directory is on sys.path so that ``rag_kb`` is importable
# when running ``python mcp/server.py`` from the project root.
_mcp_dir = str(Path(__file__).resolve().parent)
if _mcp_dir not in sys.path:
    sys.path.insert(0, _mcp_dir)

from fastmcp import FastMCP
from rag_kb.retriever import RAGRetriever

# -- Logging -----------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("mcp.server")

# -- FastMCP Server ----------------------------------------------------
mcp = FastMCP(
    "AI-Note RAG Knowledge Base",
    instructions=(
        "This server provides a RAG (Retrieval-Augmented Generation) knowledge "
        "base query interface. Use the tools to search indexed documents, add "
        "new documents, and manage the knowledge base."
    ),
)

# -- Lazy retriever initialization -------------------------------------
_retriever: RAGRetriever | None = None


def _get_retriever() -> RAGRetriever:
    """Return the singleton RAGRetriever, initializing on first call."""
    global _retriever
    if _retriever is None:
        logger.info("Initializing RAGRetriever...")
        _retriever = RAGRetriever()
    return _retriever


# -- Tools -------------------------------------------------------------

@mcp.tool
def search_knowledge(query: str, top_k: int = 5) -> str:
    """Search the RAG knowledge base for semantically relevant information.

    Use this tool to query indexed documents and retrieve context chunks
    that match the query. Returns formatted results with source metadata
    and relevance scores.

    Args:
        query: Natural language query describing what you're looking for.
        top_k: Number of top results to return (default: 5, max: 20).

    Returns:
        Formatted search results with content, source, and relevance score.
    """
    top_k = min(top_k, 20)
    retriever = _get_retriever()
    return retriever.search(query, top_k=top_k)


@mcp.tool
def add_document(file_path: str) -> str:
    """Add a document to the knowledge base for future retrieval.

    Loads the file, splits it into overlapping chunks, embeds each chunk,
    and indexes them in the vector store. Supports: txt, md, pdf, csv,
    json, html, docx, and common code files.

    Args:
        file_path: Absolute or relative path to the document.

    Returns:
        Status message with the number of chunks indexed.
    """
    retriever = _get_retriever()
    return retriever.add_file(file_path)


@mcp.tool
def add_directory(directory: str) -> str:
    """Add all supported documents from a directory to the knowledge base.

    Scans the directory for supported files (non-recursive), loads and
    indexes each one.

    Args:
        directory: Path to a directory containing documents.

    Returns:
        Status message with total chunks indexed.
    """
    retriever = _get_retriever()
    return retriever.add_directory(directory)


@mcp.tool
def list_sources() -> str:
    """List all indexed knowledge sources in the knowledge base.

    Returns each source file with its chunk count.

    Returns:
        Formatted list of indexed sources.
    """
    retriever = _get_retriever()
    return retriever.list_sources()


@mcp.tool
def get_context(query: str, top_k: int = 3) -> str:
    """Get raw context chunks for a query (for downstream processing).

    Similar to search_knowledge but returns just the concatenated content
    of the top results, suitable for use as context in a prompt.

    Args:
        query: Natural language query.
        top_k: Number of context chunks to return (default: 3).

    Returns:
        Concatenated content from the top matching chunks.
    """
    top_k = min(top_k, 10)
    retriever = _get_retriever()
    results = retriever.get_raw_context(query, top_k=top_k)
    if not results:
        return "No relevant context found."

    chunks = []
    for i, r in enumerate(results, 1):
        chunks.append(
            f"[Source {i}: {r['file_name'] or r['source']} "
            f"(relevance: {r['score']:.4f})]\n{r['content']}"
        )
    return "\n\n".join(chunks)


# -- Entry Point -------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting AI-Note RAG Knowledge Base MCP server...")
    mcp.run()
