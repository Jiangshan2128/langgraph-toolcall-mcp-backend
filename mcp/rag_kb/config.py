"""Configuration for the MCP RAG Knowledge Base service.

Uses pydantic-settings to read from ``mcp/.env`` (MCP-specific configuration).

Embedding configuration
-----------------------
Set these in ``mcp/.env`` to configure the embedding provider:

.. code-block:: env

    # Option A: Use GLM/ZhipuAI embeddings (default, uses existing GLM_API_KEY)
    EMBEDDING_PROVIDER=openai
    EMBEDDING_MODEL=embedding-2
    EMBEDDING_BASE_URL=https://open.bigmodel.cn/api/paas/v4/

    # Option B: Use OpenAI embeddings
    EMBEDDING_PROVIDER=openai
    EMBEDDING_MODEL=text-embedding-3-small
    EMBEDDING_API_KEY=sk-...

    # Option C: Use local HuggingFace embeddings
    EMBEDDING_PROVIDER=huggingface
    EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

When ``EMBEDDING_API_KEY`` is unset, the config falls back to
``GLM_API_KEY`` from the main project (if available in environment).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# MCP root (parent of rag_kb/)
_MCP_ROOT = Path(__file__).resolve().parents[1]
# Project root (for fallback to main project config)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Load root .env first (for GLM_API_KEY etc.), then overlay with mcp/.env
_load_dotenv = load_dotenv(_PROJECT_ROOT / ".env")
_load_dotenv = load_dotenv(_MCP_ROOT / ".env", override=True) or _load_dotenv


class RAGConfig(BaseSettings):
    """RAG knowledge base configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- Embeddings ----------------------------------------------------
    # Provider: "openai" (any OpenAI-compatible API) or "huggingface" (local)
    EMBEDDING_PROVIDER: str = "openai"
    EMBEDDING_MODEL: str = "embedding-2"
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_BASE_URL: str = ""

    # -- Vector Store (Qdrant) ------------------------------------------
    # Qdrant local mode: stores data at this path (no server needed).
    # Use ":memory:" for non-persistent in-memory storage.
    QDRANT_PATH: str = str(
        _PROJECT_ROOT / "mcp" / "knowledge_base" / "qdrant_data"
    )
    # Qdrant collection name
    QDRANT_COLLECTION: str = "ai_note_knowledge"
    # Distance metric: cosine, euclid, dot
    QDRANT_DISTANCE: str = "cosine"

    # -- Documents ------------------------------------------------------
    DOCUMENTS_PATH: str = str(
        _PROJECT_ROOT / "mcp" / "knowledge_base" / "documents"
    )

    # -- Chunking -------------------------------------------------------
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200

    # -- Retrieval ------------------------------------------------------
    DEFAULT_TOP_K: int = 5
    MAX_CONTEXT_LENGTH: int = 8000  # max chars of retrieved context

    def get_effective_api_key(self) -> str:
        """Return the embedding API key, falling back to GLM_API_KEY."""
        if self.EMBEDDING_API_KEY:
            return self.EMBEDDING_API_KEY
        # Try pydantic-settings model_extra first, then os.environ
        return os.environ.get("GLM_API_KEY", "")

    def get_effective_base_url(self) -> str:
        """Return the embedding base URL, falling back to GLM_BASE_URL."""
        if self.EMBEDDING_BASE_URL:
            return self.EMBEDDING_BASE_URL
        return os.environ.get("GLM_BASE_URL", "")


# Singleton
_rag_config: RAGConfig | None = None


def get_rag_config() -> RAGConfig:
    """Return the cached RAGConfig singleton, creating it on first call."""
    global _rag_config
    if _rag_config is None:
        _rag_config = RAGConfig()
    return _rag_config