"""RAG Retriever — the core retrieval orchestration module.

Wires together embeddings, vector store, and retrieval into a single
``RAGRetriever`` class used by the MCP tools.

The retriever works with the abstract ``VectorStoreInterface``, so it is
backend-agnostic — swapping from Qdrant to Supabase requires zero changes
to this file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from rag_kb.config import RAGConfig, get_rag_config
from rag_kb.embeddings import create_embeddings
from rag_kb.indexer import index_documents
from rag_kb.interfaces import IndexResult, VectorStoreInterface
from rag_kb.vector_store_factory import create_vector_store
from rag_kb.watcher import start_watcher, stop_watcher

logger = logging.getLogger(__name__)


class RAGRetriever:
    """Core RAG retriever that manages the full document lifecycle.

    Lifecycle::

        retriever = RAGRetriever()
        retriever.initialize()          # startup: create store + index
        results = retriever.search("...")
        retriever.refresh_index()       # manual re-index
        retriever.shutdown()            # shutdown
    """

    def __init__(self, config: RAGConfig | None = None) -> None:
        self._config = config or get_rag_config()
        self._store: VectorStoreInterface | None = None
        self._initialized = False

    # ── Lifecycle ────────────────────────────────────────────────────

    def initialize(self) -> IndexResult:
        """Initialize embeddings, vector store, and perform initial indexing.

        Call this once on server startup.
        """
        if self._initialized:
            logger.info("RAGRetriever already initialized, skipping.")
            return IndexResult()

        logger.info("Initializing RAGRetriever...")

        # Create embeddings
        embeddings = create_embeddings(self._config)

        # Create vector store via factory (backend-agnostic)
        self._store = create_vector_store(embeddings, self._config)

        # Auto-index on startup
        if self._config.AUTO_INDEX_ON_START:
            result = index_documents(self._store, self._config)
        else:
            result = IndexResult()
            logger.info("Auto-index is disabled (AUTO_INDEX_ON_START=false).")

        # Start file watcher if enabled
        if self._config.WATCH_ENABLED:
            start_watcher(self._store, self._config)

        self._initialized = True
        return result

    def shutdown(self) -> None:
        """Clean shutdown: stop watcher, close store."""
        if not self._initialized:
            return

        stop_watcher()
        if self._store:
            self._store.close()

        self._initialized = False
        logger.info("RAGRetriever shut down.")

    # ── Indexing ─────────────────────────────────────────────────────

    def refresh_index(self, full_rebuild: bool = False) -> IndexResult:
        """Manually trigger a re-index of the documents directory.

        Args:
            full_rebuild: If True, re-index every file (ignore hash cache).
        """
        self._ensure_initialized()
        return index_documents(self._store, self._config, full_rebuild=full_rebuild)

    # ── Retrieval ────────────────────────────────────────────────────

    def search(self, query: str, top_k: int | None = None) -> str:
        """Search the knowledge base for semantically relevant context.

        Args:
            query: Natural language search query.
            top_k: Number of top results to return (default from config).

        Returns:
            Formatted string with retrieved context chunks and source metadata.
        """
        self._ensure_initialized()
        top_k = top_k or self._config.DEFAULT_TOP_K

        logger.info("RAG search: query=%r top_k=%d", query, top_k)

        results = self._store.similarity_search(query, k=top_k)

        if not results:
            return "No relevant documents found in the knowledge base."

        return self._format_results(results, query)

    def get_raw_context(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """Return raw context chunks as structured data (for programmatic use).

        Args:
            query: Natural language search query.
            top_k: Number of top results.

        Returns:
            List of dicts with ``content``, ``source``, ``file_name``, and ``score``.
        """
        self._ensure_initialized()
        top_k = top_k or self._config.DEFAULT_TOP_K

        results = self._store.similarity_search(query, k=top_k)

        return [
            {
                "content": r.content,
                "source": r.source,
                "file_name": r.file_name,
                "score": r.score,
            }
            for r in results
        ]

    # ── Statistics ───────────────────────────────────────────────────

    def list_sources(self) -> str:
        """List all unique source files in the knowledge base.

        Returns:
            Formatted list of source file paths and chunk counts.
        """
        self._ensure_initialized()
        source_counts = self._store.list_sources()

        if not source_counts:
            return "Knowledge base is empty. Add documents to get started."

        lines = [f"Knowledge base contains {len(source_counts)} source(s):"]
        for source, count in sorted(source_counts.items()):
            lines.append(f"  - {source} ({count} chunks)")
        return "\n".join(lines)

    def get_doc_stats(self) -> dict[str, Any]:
        """Return detailed statistics about the knowledge base.

        Returns:
            Dict with ``total_chunks``, ``total_sources``, ``backend``, and ``doc_dir``.
        """
        self._ensure_initialized()
        return {
            "total_chunks": self._store.get_document_count(),
            "total_sources": len(self._store.list_sources()),
            "backend": self._config.VECTOR_STORE_BACKEND,
            "doc_dir": self._config.DOCUMENTS_PATH,
            "watch_enabled": self._config.WATCH_ENABLED,
            "auto_index": self._config.AUTO_INDEX_ON_START,
        }

    # ── Helpers ─────────────────────────────────────────────────────

    def _ensure_initialized(self) -> None:
        """Lazy initialization on first use if ``initialize()`` wasn't called."""
        if not self._initialized:
            self.initialize()

    def _format_results(
        self,
        results: list[Any],
        query: str,
    ) -> str:
        """Format retrieved documents into a readable string."""
        lines = [f"Search results for: '{query}'\n"]

        for i, r in enumerate(results, 1):
            content = r.content[:self._config.MAX_CONTEXT_LENGTH]
            truncated = "..." if len(r.content) > self._config.MAX_CONTEXT_LENGTH else ""

            lines.append(f"--- Result {i} (relevance: {r.score:.4f}) ---")
            lines.append(f"Source: {r.file_name or r.source}")
            lines.append(f"Content:\n{content}{truncated}")
            lines.append("")

        return "\n".join(lines)
