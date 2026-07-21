"""MCP tools — GraphRAG knowledge graph search.

Tools registered at import time via ``@mcp.tool``:

    search_graph   — Deep graph-based retrieval for complex queries
    get_graph_stats — View knowledge graph statistics

These complement the existing ``search_docs`` tool.  The Client LLM
chooses which tool to call based on the description:
- ``search_docs``: fast semantic search, best for concrete facts/parameters
- ``search_graph``: graph-based search, best for global overviews,
  cross-section relationships, and multi-hop reasoning
"""

from __future__ import annotations

import logging
from pathlib import Path

from rag_kb.tools import mcp

logger = logging.getLogger(__name__)

# GraphRAG store singleton (lazy init, shared across tools)
_graphrag_store = None
_GRAPHRAG_ROOT = None


def _get_graphrag_root() -> str:
    """Return the absolute path to the graphrag project directory."""
    global _GRAPHRAG_ROOT
    if _GRAPHRAG_ROOT is None:
        _mcp_dir = Path(__file__).resolve().parents[1]
        _GRAPHRAG_ROOT = str(_mcp_dir / "graphrag")
    return _GRAPHRAG_ROOT


def _get_store():
    """Get or create the GraphRagStore singleton."""
    global _graphrag_store
    if _graphrag_store is None:
        from rag_kb.graphrag_store import GraphRagStore

        root = _get_graphrag_root()
        _graphrag_store = GraphRagStore(root)
        stats = _graphrag_store.get_stats()
        if stats.is_ready:
            logger.info(
                "GraphRAG store ready: %d entities, %d relationships, %d communities",
                stats.entity_count,
                stats.relationship_count,
                stats.community_count,
            )
        else:
            logger.info("GraphRAG store loaded but index not built yet")
    return _graphrag_store


# ── Tools ─────────────────────────────────────────────────────────────


@mcp.tool
def search_graph(query: str) -> str:
    """Deep graph-based knowledge retrieval for complex queries.

    Use this tool when the question involves ANY of the following:
    - **Global overview**: "文档整体讲了什么", "涉及哪些技术领域"
    - **Cross-section relationships**: "A 如何影响 B", "X 对 Y 有什么约束"
    - **Multi-hop reasoning**: "在载荷限制下能扩展什么功能"
    - **Cross-document comparison**: "三个文档中哪些参数待定"
    - **Summarization**: "总结这份规格书的验收体系"

    For simple fact/parameter lookups (e.g. "机芯供电电压", "云台定位精度"),
    use ``search_docs`` instead — it is faster and more cost-effective.

    Note: This tool requires the GraphRAG index to be built first.
    If indexing has not been run, it will suggest using ``search_docs``.

    Args:
        query: Natural language query for graph-based retrieval.

    Returns:
        Formatted answer generated from the knowledge graph.
    """
    store = _get_store()
    return store.search(query)


@mcp.tool
def get_graph_stats() -> str:
    """View statistics about the GraphRAG knowledge graph.

    Returns entity count, relationship count, community count, and
    whether the index is ready for querying.  Use this to verify
    the knowledge graph has been built before calling ``search_graph``.

    Returns:
        Formatted statistics about the knowledge graph.
    """
    store = _get_store()
    stats = store.get_stats()

    lines = ["GraphRAG Knowledge Graph Statistics", "-" * 40]
    lines.append(f"  Ready:        {'Yes' if stats.is_ready else 'No (index not built)'}")
    lines.append(f"  Entities:     {stats.entity_count}")
    lines.append(f"  Relationships:{stats.relationship_count}")
    lines.append(f"  Communities:  {stats.community_count}")
    lines.append(f"  Text Units:   {stats.text_unit_count}")
    lines.append(f"  Documents:    {stats.document_count}")

    if not stats.is_ready:
        root = _get_graphrag_root()
        lines.append("")
        lines.append("To build the index, run:")
        lines.append(f"  graphrag index --root {root}")

    return "\n".join(lines)
