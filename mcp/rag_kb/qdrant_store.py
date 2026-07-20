"""Qdrant vector store backend — local mode with hybrid search.

Implements ``VectorStoreInterface`` using Qdrant in local (no-server) mode.

**Two search modes** — selected automatically by the store:

==================================== ============================================
Embeddings type                      Search mode
==================================== ============================================
``BgeM3Embeddings`` (has sparse)     **Hybrid** — dense + sparse vectors stored
                                     in named-vector collection; query uses RRF
                                     fusion over two parallel prefetch lanes.
Plain ``Embeddings`` (dense only)    **Dense-only** — same as before, via named
                                     vector ``"dense"``.
==================================== ============================================

Data is persisted on disk at the configured path.
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

# Named-vector keys used in the Qdrant collection schema.
_DENSE_KEY = "dense"
_SPARSE_KEY = "sparse"

# How many candidates each prefetch lane returns before RRF fusion.
# Best practice from Qdrant docs: max(20, min(100, limit * multiplier)).
_PREFETCH_MULTIPLIER = 3
_PREFETCH_CAP = 100

# RRF constant — smaller = top-ranked results weighted more heavily.
# 60 is the standard from the original RRF paper and Qdrant's recommendation.
_RRF_K = 60


class QdrantStore(VectorStoreInterface):
    """Qdrant-backed vector store (local mode, no server needed).

    When paired with a sparse-capable embeddings model (e.g. ``BgeM3Embeddings``),
    the store automatically creates a **named-vector collection** with both
    ``"dense"`` and ``"sparse"`` vectors, and queries use reciprocal-rank fusion
    to combine results from both lanes.

    With a plain dense-only embeddings model the store still uses named vectors
    (``"dense"``), making the on-disk format consistent.
    """

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

        # Detect sparse support on the embeddings object.
        self._has_sparse = hasattr(self._embeddings, "embed_with_sparse")

        # Initialize client and collection
        self._client = QdrantClient(path=str(self._persist_path))
        self._ensure_collection()

    # ── Lifecycle ────────────────────────────────────────────────────

    def close(self) -> None:
        """Qdrant local mode flushes on every write; nothing explicit needed."""
        logger.info("Closing Qdrant store (collection=%s)", self._collection_name)

    # ── Indexing ─────────────────────────────────────────────────────

    def add_documents(self, chunks: list[IndexedDoc]) -> int:
        """Embed and index chunks into Qdrant.

        When sparse is available, encodes both dense and sparse vectors in a
        single pass via ``embed_with_sparse()`` for efficiency.
        """
        if not chunks:
            return 0

        BATCH = 10  # text-embedding-v4 API limit: 10 texts per call
        points: list[models.PointStruct] = []

        for offset in range(0, len(chunks), BATCH):
            batch = chunks[offset : offset + BATCH]
            texts = [c.content for c in batch]

            if self._has_sparse:
                dense_list, sparse_list = self._embeddings.embed_with_sparse(texts)
            else:
                dense_list = self._embeddings.embed_documents(texts)
                sparse_list = None

            for i, chunk in enumerate(batch):
                point_id = chunk.id or str(uuid4())
                vector_dict: dict = {
                    _DENSE_KEY: _ensure_list(dense_list[i]),
                }

                if sparse_list:
                    sw = sparse_list[i]  # dict[int, float]
                    vector_dict[_SPARSE_KEY] = models.SparseVector(
                        indices=list(sw.keys()),
                        values=list(sw.values()),
                    )

                points.append(
                    models.PointStruct(
                        id=point_id,
                        vector=vector_dict,
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
        logger.info(
            "Indexed %d chunk(s) into Qdrant (sparse=%s)",
            len(points), self._has_sparse,
        )
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

        logger.info(
            "Deleted %d chunk(s) for source '%s'",
            len(ids_to_delete), source,
        )
        return len(ids_to_delete)

    # ── Retrieval ────────────────────────────────────────────────────

    def similarity_search(self, query: str, k: int = 5) -> list[SearchResult]:
        """Search by embedding similarity.

        Routes to hybrid (dense + sparse → RRF) or dense-only depending on
        whether the embeddings model provides sparse vectors.
        """
        if self._has_sparse:
            return self._hybrid_search(query, k)
        return self._dense_search(query, k)

    def _dense_search(self, query: str, k: int) -> list[SearchResult]:
        """Dense-only semantic search (fallback for non-sparse embeddings)."""
        query_vec = self._embeddings.embed_query(query)
        result = self._client.query_points(
            collection_name=self._collection_name,
            query=query_vec,
            using=_DENSE_KEY,
            limit=k,
            with_payload=True,
        )
        return _hits_to_results(result.points)

    def _hybrid_search(self, query: str, k: int) -> list[SearchResult]:
        """Hybrid dense + sparse search with Reciprocal Rank Fusion.

        1. Encode the query → dense vector + sparse token weights.
        2. Run two *independent* prefetch lanes in Qdrant:
           - ``"dense"`` — semantic similarity
           - ``"sparse"`` — lexical / keyword match (neural BM25)
        3. Fuse the two ranked lists via RRF.
        4. Return the top *k* merged results.
        """
        dense_vec = self._embeddings.embed_query(query)
        sparse_dicts = self._embeddings.encode_sparse([query], text_type="query")
        sparse_dict = sparse_dicts[0]

        prefetch_limit = max(20, min(_PREFETCH_CAP, k * _PREFETCH_MULTIPLIER))

        result = self._client.query_points(
            collection_name=self._collection_name,
            prefetch=[
                # Lane 1 — semantic (dense)
                models.Prefetch(
                    query=dense_vec,
                    using=_DENSE_KEY,
                    limit=prefetch_limit,
                ),
                # Lane 2 — keyword / lexical (sparse, neural BM25)
                models.Prefetch(
                    query=models.SparseVector(
                        indices=list(sparse_dict.keys()),
                        values=list(sparse_dict.values()),
                    ),
                    using=_SPARSE_KEY,
                    limit=prefetch_limit,
                ),
            ],
            query=models.RrfQuery(rrf=models.Rrf(k=_RRF_K)),
            limit=k,
            with_payload=True,
        )
        return _hits_to_results(result.points)

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
                source = (
                    metadata.get("source", "unknown")
                    if isinstance(metadata, dict)
                    else "unknown"
                )
                source_counts[source] = source_counts.get(source, 0) + 1

            if next_offset is None:
                break
            offset = next_offset

        return source_counts

    # ── Internal helpers ─────────────────────────────────────────────

    def _ensure_collection(self) -> None:
        """Create or validate the Qdrant collection.

        Rules:
        - No collection → create with the schema that matches our embeddings.
        - Collection exists & schema matches → re-use.
        - Collection exists & schema **mismatches** (e.g. old unnamed-vector
          collection before the hybrid-search upgrade) → drop and recreate.
          The documents directory will be re-indexed on next startup.
        """
        self._persist_path.mkdir(parents=True, exist_ok=True)

        if not self._client.collection_exists(self._collection_name):
            self._create_collection()
            return

        # ── Schema validation ────────────────────────────────────────
        info = self._client.get_collection(self._collection_name)
        config = info.config.params
        store_has_sparse = bool(config.sparse_vectors)

        if self._has_sparse != store_has_sparse:
            logger.warning(
                "Collection '%s' schema mismatch (embeddings sparse=%s, "
                "store sparse=%s) — dropping and recreating",
                self._collection_name, self._has_sparse, store_has_sparse,
            )
            self._client.delete_collection(self._collection_name)
            self._create_collection()
        else:
            logger.info(
                "Using existing Qdrant collection '%s' at %s (sparse=%s)",
                self._collection_name, self._persist_path, self._has_sparse,
            )

    def _create_collection(self) -> None:
        """Create a new collection with the appropriate vector schema."""
        vector_size = len(self._embeddings.embed_query("init"))

        # Always use named vectors for consistency.
        vectors_config = {
            _DENSE_KEY: models.VectorParams(
                size=vector_size,
                distance=self._distance_metric,
            ),
        }

        sparse_config: dict | None = None
        if self._has_sparse:
            sparse_config = {
                _SPARSE_KEY: models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False),
                ),
            }

        logger.info(
            "Creating Qdrant collection '%s' (dim=%d, metric=%s, sparse=%s) at %s",
            self._collection_name, vector_size,
            self._distance_metric, self._has_sparse, self._persist_path,
        )

        kwargs: dict = {
            "collection_name": self._collection_name,
            "vectors_config": vectors_config,
        }
        if sparse_config:
            kwargs["sparse_vectors_config"] = sparse_config

        self._client.create_collection(**kwargs)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _hits_to_results(hits: list) -> list[SearchResult]:
    """Convert Qdrant scored-point hits to our agnostic ``SearchResult``."""
    results: list[SearchResult] = []
    for hit in hits:
        payload = hit.payload or {}
        metadata = payload.get("metadata", {}) or {}
        if not isinstance(metadata, dict):
            metadata = {}

        results.append(
            SearchResult(
                content=payload.get("content", ""),
                source=metadata.get("source", "unknown"),
                file_name=metadata.get("file_name", ""),
                score=hit.score or 0.0,
                metadata=metadata,
            )
        )
    return results


def _ensure_list(vec: list[float] | object) -> list[float]:
    """Coerce a vector to ``list[float]`` (handles numpy arrays)."""
    if hasattr(vec, "tolist"):
        return vec.tolist()  # type: ignore[union-attr]
    return list(vec)  # type: ignore[arg-type]
