"""RAG Retriever — the core retrieval + generation orchestration module.

Wires together embeddings, vector store, document loading, and retrieval
into a single ``RAGRetriever`` class used by the MCP tools.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from rag_kb.config import RAGConfig, get_rag_config
from rag_kb.embeddings import create_embeddings
from rag_kb.loader import load_directory, load_file
from rag_kb.splitter import split_documents
from rag_kb.vector_store import (
    add_to_store,
    create_vector_store,
    delete_by_source,
    get_document_count,
    list_sources as list_store_sources,
)

logger = logging.getLogger(__name__)


class RAGRetriever:
    """Core RAG retriever that manages the full document lifecycle.

    Loads documents, splits them, indexes into a vector store, and provides
    semantic search with formatted context output.

    Usage::

        retriever = RAGRetriever()
        results = retriever.search("What is LangGraph?")
        retriever.add_file("/path/to/doc.pdf")
        sources = retriever.list_sources()
    """

    def __init__(self, config: RAGConfig | None = None) -> None:
        self._config = config or get_rag_config()
        self._embeddings: Embeddings | None = None
        self._vector_store: VectorStore | None = None

    # ── Lazy initialization ────────────────────────────────────────

    @property
    def embeddings(self) -> Embeddings:
        if self._embeddings is None:
            self._embeddings = create_embeddings(self._config)
        return self._embeddings

    @property
    def vector_store(self) -> VectorStore:
        if self._vector_store is None:
            self._vector_store = create_vector_store(
                self.embeddings,
                self._config.QDRANT_PATH,
                collection_name=self._config.QDRANT_COLLECTION,
                distance=self._config.QDRANT_DISTANCE,
            )
        return self._vector_store

    # ── Public API ──────────────────────────────────────────────────

    def search(self, query: str, top_k: int | None = None) -> str:
        """Search the knowledge base for relevant context.

        Args:
            query: Natural language search query.
            top_k: Number of top results to return (default from config).

        Returns:
            Formatted string with retrieved context chunks and source metadata.
        """
        top_k = top_k or self._config.DEFAULT_TOP_K

        logger.info("RAG search: query=%r top_k=%d", query, top_k)

        store = self.vector_store
        docs_with_scores: list[tuple[Document, float]] = store.similarity_search_with_score(query, k=top_k)

        if not docs_with_scores:
            return "No relevant documents found in the knowledge base."

        return self._format_results(docs_with_scores, query)

    def add_file(self, file_path: str) -> str:
        """Add a single file to the knowledge base.

        Args:
            file_path: Path to the file to add.

        Returns:
            Status message with the number of chunks added.
        """
        file_path_str = str(Path(file_path).resolve())

        # Load
        docs = load_file(file_path_str)

        # Split
        chunks = split_documents(docs, self._config)

        # Index
        count = add_to_store(
            self.vector_store,
            chunks,
        )

        total = get_document_count(self.vector_store)
        return (
            f"Added {count} chunk(s) from '{Path(file_path_str).name}' "
            f"to the knowledge base. Total indexed chunks: {total}."
        )

    def add_directory(self, directory: str) -> str:
        """Add all supported files from a directory to the knowledge base.

        Args:
            directory: Path to the directory.

        Returns:
            Status message with total chunks indexed.
        """
        docs = load_directory(directory)

        if not docs:
            return f"No supported files found in directory: {directory}"

        chunks = split_documents(docs, self._config)
        count = add_to_store(self.vector_store, chunks)

        total = get_document_count(self.vector_store)
        return (
            f"Added {count} chunk(s) from directory '{directory}' "
            f"to the knowledge base. Total indexed chunks: {total}."
        )

    def list_sources(self) -> str:
        """List all unique source files in the knowledge base.

        Returns:
            Formatted list of source file paths and chunk counts.
        """
        source_counts = list_store_sources(self.vector_store)

        if not source_counts:
            return "Knowledge base is empty. Add documents to get started."

        lines = [f"Knowledge base contains {len(source_counts)} source(s):"]
        for source, count in sorted(source_counts.items()):
            lines.append(f"  - {source} ({count} chunks)")
        return "\n".join(lines)

    def get_raw_context(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """Return raw context chunks as structured data (for programmatic use).

        Args:
            query: Natural language search query.
            top_k: Number of top results.

        Returns:
            List of dicts with ``content``, ``source``, and ``score`` keys.
        """
        top_k = top_k or self._config.DEFAULT_TOP_K

        store = self.vector_store
        docs_with_scores = store.similarity_search_with_score(query, k=top_k)

        return [
            {
                "content": doc.page_content,
                "source": doc.metadata.get("source", "unknown"),
                "file_name": doc.metadata.get("file_name", ""),
                "score": round(score, 4),
            }
            for doc, score in docs_with_scores
        ]

    # ── Helpers ─────────────────────────────────────────────────────

    def _format_results(
        self,
        docs_with_scores: list[tuple[Document, float]],
        query: str,
    ) -> str:
        """Format retrieved documents into a readable string."""
        lines = [f"Search results for: '{query}'\n"]

        for i, (doc, score) in enumerate(docs_with_scores, 1):
            source = doc.metadata.get("source", "unknown")
            file_name = doc.metadata.get("file_name", "")
            content = doc.page_content[:self._config.MAX_CONTEXT_LENGTH]

            # Truncate indicator
            truncated = "..." if len(doc.page_content) > self._config.MAX_CONTEXT_LENGTH else ""

            lines.append(f"--- Result {i} (relevance: {score:.4f}) ---")
            lines.append(f"Source: {file_name or source}")
            lines.append(f"Content:\n{content}{truncated}")
            lines.append("")

        return "\n".join(lines)
