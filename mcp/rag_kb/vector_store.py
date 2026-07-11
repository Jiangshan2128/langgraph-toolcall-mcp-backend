"""Vector store — Qdrant local mode for persistent document storage.

Uses Qdrant in local mode (``QdrantClient(path=...)``) — no server process
required, data is stored on disk.  This gives us:

- True persistence (survives process restarts)
- Rich metadata filtering (Qdrant's filter DSL)
- Better performance than FAISS for typical workloads
- Optional upgrade path to Qdrant Cloud / self-hosted server
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from qdrant_client import QdrantClient, models
from qdrant_client.http.models import Distance

logger = logging.getLogger(__name__)

# Distance metric mapping
_DISTANCE_MAP: dict[str, Distance] = {
    "cosine": Distance.COSINE,
    "euclid": Distance.EUCLID,
    "dot": Distance.DOT,
}


def create_vector_store(
    embeddings: Embeddings,
    persist_path: str | Path,
    collection_name: str = "ai_note_knowledge",
    distance: str = "cosine",
) -> VectorStore:
    """Create or load a persistent Qdrant vector store (local mode).

    If a saved collection exists at ``persist_path``, it is loaded.
    Otherwise a new collection is created automatically.

    Args:
        embeddings: Embedding model for vectorization.
        persist_path: Directory path for Qdrant on-disk storage.
        collection_name: Name of the Qdrant collection.
        distance: Distance metric — ``cosine``, ``euclid``, or ``dot``.

    Returns:
        A ``QdrantVectorStore`` (LangChain-compatible ``VectorStore``).
    """
    from langchain_qdrant import QdrantVectorStore

    persist_path = Path(persist_path)
    persist_path.mkdir(parents=True, exist_ok=True)

    distance_metric = _DISTANCE_MAP.get(distance, Distance.COSINE)
    client = QdrantClient(path=str(persist_path))

    # Auto-create collection if it doesn't exist
    if not client.collection_exists(collection_name):
        # Determine vector size by embedding a sample text
        vector_size = len(embeddings.embed_query("init"))
        logger.info(
            "Creating Qdrant collection '%s' (dim=%d, metric=%s) at %s",
            collection_name, vector_size, distance, persist_path,
        )
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=distance_metric,
            ),
        )
    else:
        logger.info(
            "Loading existing Qdrant collection '%s' from %s",
            collection_name, persist_path,
        )

    store = QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=embeddings,
    )
    return store


def add_to_store(
    store: VectorStore,
    documents: list[Document],
) -> int:
    """Add documents to the vector store.

    Qdrant persists automatically — no explicit save step needed.

    Args:
        store: The QdrantVectorStore instance.
        documents: Chunked documents to add.

    Returns:
        Number of documents added.
    """
    if not documents:
        return 0

    from uuid import uuid4

    ids = [str(uuid4()) for _ in documents]
    store.add_documents(documents=documents, ids=ids)

    logger.info("Added %d document(s) to vector store", len(documents))
    return len(documents)


def get_document_count(store: VectorStore) -> int:
    """Return the number of indexed documents.

    Uses Qdrant's collection info to get the exact point count.
    """
    try:
        from langchain_qdrant import QdrantVectorStore

        if isinstance(store, QdrantVectorStore):
            info = store.client.get_collection(store.collection_name)
            return info.points_count
    except Exception:
        pass
    return 0


def list_sources(store: VectorStore) -> dict[str, int]:
    """List all unique source files in the knowledge base with chunk counts.

    Uses Qdrant's scroll API to iterate through all points (documents).

    Args:
        store: The QdrantVectorStore instance.

    Returns:
        Dict mapping source path → chunk count.
    """
    from langchain_qdrant import QdrantVectorStore

    if not isinstance(store, QdrantVectorStore):
        return {}

    source_counts: dict[str, int] = {}
    offset = None

    while True:
        points, next_offset = store.client.scroll(
            collection_name=store.collection_name,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            metadata = (point.payload or {}).get("metadata", {})
            source = metadata.get("source", "unknown") if isinstance(metadata, dict) else "unknown"
            source_counts[source] = source_counts.get(source, 0) + 1

        if next_offset is None:
            break
        offset = next_offset

    return source_counts


def delete_by_source(store: VectorStore, source: str) -> int:
    """Delete all documents from a specific source file.

    Args:
        store: The QdrantVectorStore instance.
        source: Source file path to delete.

    Returns:
        Number of points deleted.
    """
    from langchain_qdrant import QdrantVectorStore

    if not isinstance(store, QdrantVectorStore):
        return 0

    # Scroll to find points matching the source
    ids_to_delete: list[str] = []
    offset = None

    while True:
        points, next_offset = store.client.scroll(
            collection_name=store.collection_name,
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
        store.delete(ids=ids_to_delete)

    logger.info("Deleted %d point(s) for source '%s'", len(ids_to_delete), source)
    return len(ids_to_delete)