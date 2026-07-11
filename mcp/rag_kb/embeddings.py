"""Embedding model factory.

Supports:
- OpenAI-compatible API embeddings (default, works with any provider)
- HuggingFace local embeddings (via sentence-transformers)
"""

from __future__ import annotations

import logging

from langchain_core.embeddings import Embeddings

from rag_kb.config import RAGConfig

logger = logging.getLogger(__name__)


def create_embeddings(config: RAGConfig) -> Embeddings:
    """Create an embedding model based on configuration.

    Args:
        config: RAG configuration instance.

    Returns:
        A LangChain ``Embeddings`` instance.

    Raises:
        ValueError: If the provider is unsupported.
    """
    provider = config.EMBEDDING_PROVIDER.lower()

    if provider == "openai":
        return _create_openai_embeddings(config)
    elif provider == "huggingface":
        return _create_huggingface_embeddings(config)
    else:
        raise ValueError(
            f"Unsupported embedding provider: {provider!r}. "
            f"Choose 'openai' or 'huggingface'."
        )


def _create_openai_embeddings(config: RAGConfig) -> Embeddings:
    """Create OpenAI-compatible embeddings.

    Uses the ``langchain-openai`` package (already in project dependencies).
    Supports any OpenAI-compatible API by setting ``EMBEDDING_BASE_URL``.
    Falls back to ``GLM_API_KEY`` / ``GLM_BASE_URL`` when no explicit
    embedding credentials are configured.
    """
    from langchain_openai import OpenAIEmbeddings

    api_key = config.get_effective_api_key()
    base_url = config.get_effective_base_url()

    kwargs: dict = {"model": config.EMBEDDING_MODEL}

    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    logger.info(
        "Using OpenAI-compatible embeddings: model=%s base_url=%s",
        config.EMBEDDING_MODEL,
        base_url or "(default)",
    )
    return OpenAIEmbeddings(**kwargs)


def _create_huggingface_embeddings(config: RAGConfig) -> Embeddings:
    """Create local HuggingFace embeddings via sentence-transformers.

    Requires: ``pip install langchain-huggingface sentence-transformers``
    """
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except ImportError:
        raise ImportError(
            "langchain-huggingface is required for HuggingFace embeddings. "
            "Install: pip install langchain-huggingface sentence-transformers"
        )

    model_name = config.EMBEDDING_MODEL or "sentence-transformers/all-MiniLM-L6-v2"
    logger.info("Using HuggingFace local embeddings: model=%s", model_name)
    return HuggingFaceEmbeddings(model_name=model_name)
