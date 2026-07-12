"""Abstract interfaces for vector store backends.

Defines a ``VectorStoreInterface`` that all vector store implementations
must conform to.  This decouples the RAG orchestration layer from any
specific vector database (Qdrant, Supabase/pgvector, Chroma, etc.).

To add a new backend:
    1. Implement ``VectorStoreInterface`` in a new module.
    2. Register it in ``create_vector_store()`` factory.
    3. Update config with any new env vars.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IndexedDoc:
    """A single document chunk ready for indexing.

    This is the internal representation used to pass data into the
    vector store.  It is backend-agnostic.
    """

    content: str
    metadata: dict[str, Any]
    id: str = ""


@dataclass
class SearchResult:
    """A single search result returned from a similarity search.

    Fields:
        content: The text content of the matched chunk.
        source: Absolute file path of the source document.
        file_name: Base file name (for display).
        score: Similarity score (0-1, higher = more relevant).
        metadata: Full metadata payload from the store.
    """

    content: str
    source: str
    file_name: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IndexResult:
    """Summary of an indexing operation."""

    total_files: int = 0
    indexed_files: int = 0
    skipped_files: int = 0
    deleted_files: int = 0
    total_chunks: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Human-readable summary of the indexing result."""
        parts = [
            f"Indexed {self.indexed_files} file(s)" if self.indexed_files else None,
            f"skipped {self.skipped_files} unchanged" if self.skipped_files else None,
            f"removed {self.deleted_files} stale" if self.deleted_files else None,
        ]
        detail = ", ".join(p for p in parts if p)
        msg = (
            f"{self.total_chunks} chunk(s) from {self.total_files} file(s)"
        )
        if detail:
            msg += f" ({detail})"
        if self.errors:
            msg += f" with {len(self.errors)} error(s)"
        return msg


class VectorStoreInterface(ABC):
    """Abstract vector store — all backends implement this.

    Lifecycle::

        store = create_vector_store(config)
        store.add_documents(chunks)      # indexer
        results = store.similarity_search(query, k)   # retriever
        store.close()                    # shutdown
    """

    # ── Lifecycle ────────────────────────────────────────────────────

    @abstractmethod
    def close(self) -> None:
        """Release resources (client connections, file handles)."""
        ...

    # ── Indexing ─────────────────────────────────────────────────────

    @abstractmethod
    def add_documents(self, chunks: list[IndexedDoc]) -> int:
        """Index a batch of document chunks.

        Args:
            chunks: Document chunks to add.

        Returns:
            Number of chunks successfully indexed.
        """
        ...

    @abstractmethod
    def delete_by_source(self, source: str) -> int:
        """Remove all chunks belonging to a source file.

        Args:
            source: Source file path to delete.

        Returns:
            Number of points deleted.
        """
        ...

    # ── Retrieval ────────────────────────────────────────────────────

    @abstractmethod
    def similarity_search(self, query: str, k: int = 5) -> list[SearchResult]:
        """Search for the top-k most similar chunks.

        Args:
            query: Natural language query.
            k: Number of results to return.

        Returns:
            Ordered list of search results (highest score first).
        """
        ...

    # ── Statistics ───────────────────────────────────────────────────

    @abstractmethod
    def get_document_count(self) -> int:
        """Return total number of indexed chunks."""
        ...

    @abstractmethod
    def list_sources(self) -> dict[str, int]:
        """List all unique source files with chunk counts.

        Returns:
            Mapping of source path → chunk count.
        """
        ...
