"""Vector store factory — create a vector store backend by config.

Register new backends here.  To add Supabase/pgvector support:

    1. Create ``rag_kb/supabase_store.py`` implementing ``VectorStoreInterface``.
    2. Add a ``"supabase"`` branch in ``create_vector_store()`` below.
    3. Add any new config fields to ``RAGConfig``.
"""

from __future__ import annotations

import logging

from langchain_core.embeddings import Embeddings

from rag_kb.config import RAGConfig
from rag_kb.interfaces import VectorStoreInterface

logger = logging.getLogger(__name__)


def create_vector_store(
    embeddings: Embeddings,
    config: RAGConfig,
) -> VectorStoreInterface:
    """Create a vector store backend based on configuration.

    The backend is selected by ``config.VECTOR_STORE_BACKEND``:

    =========== =========================================================
    Value       Backend
    =========== =========================================================
    ``qdrant``  Qdrant local mode (default) — no server, data on disk.
    ``supabase`` Supabase/pgvector (future) — requires ``SUPABASE_*`` env.
    =========== =========================================================
    """
    backend = config.VECTOR_STORE_BACKEND.lower()

    if backend == "qdrant":
        from rag_kb.qdrant_store import QdrantStore

        logger.info(
            "Creating Qdrant vector store: path=%s collection=%s",
            config.QDRANT_PATH, config.QDRANT_COLLECTION,
        )
        return QdrantStore(
            embeddings=embeddings,
            persist_path=config.QDRANT_PATH,
            collection_name=config.QDRANT_COLLECTION,
            distance=config.QDRANT_DISTANCE,
        )

    elif backend == "supabase":
        raise NotImplementedError(
            "Supabase vector store backend is not yet implemented. "
            "Set VECTOR_STORE_BACKEND=qdrant to use the local Qdrant backend."
        )

    else:
        raise ValueError(
            f"Unsupported vector store backend: {backend!r}. "
            f"Choose 'qdrant' or 'supabase'."
        )
