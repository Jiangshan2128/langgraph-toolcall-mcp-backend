"""Qdrant vector store backend — local mode implementation.

Implements ``VectorStoreInterface`` using Qdrant in local (no-server) mode.
Data is persisted on disk at the configured path.

To swap to Supabase/pgvector, implement ``VectorStoreInterface`` in a new
module and register it in ``vector_store_factory.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from qdrant_client import QdrantClient, models
from qdrant_client.http.models import Distance

from rag_kb.interfaces import IndexedDoc, SearchResult, VectorStoreInterface

logger = logging.getLogger(__name__)

_DISTANCE_MAP: dict[str, Distance] = {
    "cosine": Distance.COSINE,
    "euclid": Distance.EUCLID,
    "dot": Distance.DOT,
}


class QdrantStore(VectorStoreInterface):
    """Qdrant-backed vector store (local mode, no server needed)."""

    def __init__(
        self,
        embeddings: Embeddings,
        persist_path: str | Path,
        collection_name: str = "ai_note_knowledge",
        distance: str = "cosine",
    ) -> None:
        self._embeddings = embeddings
        self._persist_path = Path(persist_path)
        self._collection_name = collection_name
        self._distance_metric = _DISTANCE_MAP.get(distance, Distance.COSINE)

        # Initialize client and collection
        self._client = QdrantClient(path=str(self._persist_path))
        self._ensure_collection()

    # ── Lifecycle ────────────────────────────────────────────────────

    def close(self) -> None:
        """Qdrant local mode flushes on every write; nothing explicit needed."""
        logger.info("Closing Qdrant store (collection=%s)", self._collection_name)

    # ── Indexing ─────────────────────────────────────────────────────

    def add_documents(self, chunks: list[IndexedDoc]) -> int:
        """Embed and index chunks into Qdrant."""
        if not chunks:
            return 0

        # Prepare points
        points: list[models.PointStruct] = []
        for chunk in chunks:
            vector = self._embeddings.embed_query(chunk.content)
            point_id = chunk.id or str(uuid4())
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "content": chunk.content,
                        "metadata": chunk.metadata,
                    },
                )
            )

        self._client.upsert(
            collection_name=self._collection_name,
            points=points,
        )
        logger.info("Indexed %d chunk(s) into Qdrant", len(points))
        return len(points)

    def delete_by_source(self, source: str) -> int:
        """Remove all chunks from a specific source file."""
        ids_to_delete: list[str] = []
        offset: str | None = None

        while True:
            points, next_offset = self._client.scroll(
                collection_name=self._collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="metadata.source",
                            match=models.MatchValue(value=source),
                        )
                    ]
                ),
                limit=100,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            ids_to_delete.extend(p.id for p in points)
            if next_offset is None:
                break
            offset = next_offset

        if ids_to_delete:
            self._client.delete(
                collection_name=self._collection_name,
                points_selector=models.PointIdsList(
                    points=ids_to_delete,
                ),
            )

        logger.info("Deleted %d chunk(s) for source '%s'", len(ids_to_delete), source)
        return len(ids_to_delete)

    # ── Retrieval ────────────────────────────────────────────────────

    def similarity_search(self, query: str, k: int = 5) -> list[SearchResult]:
        """Search by embedding similarity."""
        query_vector = self._embeddings.embed_query(query)

        # qdrant-client >=1.12: use query_points() instead of deprecated search()
        # qdrant-client <1.12:  use search()
        if hasattr(self._client, "query_points"):
            result = self._client.query_points(
                collection_name=self._collection_name,
                query=query_vector,
                limit=k,
                with_payload=True,
            )
            hits = result.points
        else:
            hits = self._client.search(
                collection_name=self._collection_name,
                query_vector=query_vector,
                limit=k,
                with_payload=True,
            )

        results: list[SearchResult] = []
        for hit in hits:
            payload = hit.payload or {}
            metadata = payload.get("metadata", {}) or {}
            source = metadata.get("source", "unknown") if isinstance(metadata, dict) else "unknown"
            file_name = metadata.get("file_name", "") if isinstance(metadata, dict) else ""

            results.append(
                SearchResult(
                    content=payload.get("content", ""),
                    source=source,
                    file_name=file_name,
                    score=hit.score or 0.0,
                    metadata=metadata if isinstance(metadata, dict) else {},
                )
            )

        return results

    # ── Statistics ───────────────────────────────────────────────────

    def get_document_count(self) -> int:
        """Return total point count from collection info."""
        try:
            info = self._client.get_collection(self._collection_name)
            return info.points_count
        except Exception:
            return 0

    def list_sources(self) -> dict[str, int]:
        """Iterate all points and tally by source file."""
        source_counts: dict[str, int] = {}
        offset: str | None = None

        while True:
            points, next_offset = self._client.scroll(
                collection_name=self._collection_name,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                metadata = payload.get("metadata", {}) or {}
                source = metadata.get("source", "unknown") if isinstance(metadata, dict) else "unknown"
                source_counts[source] = source_counts.get(source, 0) + 1

            if next_offset is None:
                break
            offset = next_offset

        return source_counts

    # ── Internal helpers ─────────────────────────────────────────────

    def _ensure_collection(self) -> None:
        """Create the Qdrant collection if it doesn't exist yet."""
        self._persist_path.mkdir(parents=True, exist_ok=True)

        if self._client.collection_exists(self._collection_name):
            logger.info(
                "Using existing Qdrant collection '%s' at %s",
                self._collection_name, self._persist_path,
            )
            return

        # Determine vector size by embedding a sample
        vector_size = len(self._embeddings.embed_query("init"))
        logger.info(
            "Creating Qdrant collection '%s' (dim=%d, metric=%s) at %s",
            self._collection_name, vector_size, self._distance_metric, self._persist_path,
        )
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=self._distance_metric,
            ),
        )
