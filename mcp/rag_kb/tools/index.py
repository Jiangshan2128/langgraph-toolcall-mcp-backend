"""MCP tools — index management.

Tools registered at import time via ``@mcp.tool``:

    refresh_index     — Manually trigger a re-index of the docs directory
    get_doc_stats     — View knowledge base statistics
"""

from __future__ import annotations

import logging

from rag_kb.tools import get_retriever, mcp

logger = logging.getLogger(__name__)


@mcp.tool
def refresh_index(full_rebuild: bool = False) -> str:
    """Re-index all documents from the documents directory.

    Normally, only new/changed files are re-indexed (using a content hash
    cache).  Use ``full_rebuild=true`` to re-index every file from scratch.

    Args:
        full_rebuild: If true, re-index everything (ignores cache).

    Returns:
        Summary of what was indexed.
    """
    retriever = get_retriever()
    result = retriever.refresh_index(full_rebuild=full_rebuild)

    msg = f"Index refreshed: {result.summary}"
    if result.errors:
        msg += f"\nErrors ({len(result.errors)}):"
        for err in result.errors[:5]:
            msg += f"\n  - {err}"
    return msg


@mcp.tool
def get_doc_stats() -> str:
    """Get statistics about the knowledge base.

    Returns total indexed chunks, number of unique sources, backend type,
    and configuration status.

    Returns:
        Formatted statistics about the knowledge base.
    """
    retriever = get_retriever()
    stats = retriever.get_doc_stats()

    lines = [
        "📊 Knowledge Base Statistics",
        f"  Backend:          {stats['backend']}",
        f"  Total chunks:     {stats['total_chunks']}",
        f"  Total sources:    {stats['total_sources']}",
        f"  Documents dir:    {stats['doc_dir']}",
        f"  Auto-index:       {'✅' if stats['auto_index'] else '❌'}",
        f"  File watching:    {'✅' if stats['watch_enabled'] else '❌'}",
    ]
    return "\n".join(lines)
