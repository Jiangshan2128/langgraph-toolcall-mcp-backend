"""Embedding model factory.

Supports:
- Qwen text-embedding-v4 via DashScope SDK (dense + sparse hybrid)
- OpenAI-compatible API embeddings (works with any provider)
- HuggingFace local embeddings (via sentence-transformers)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from langchain_core.embeddings import Embeddings

from rag_kb.config import RAGConfig

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


def create_embeddings(config: RAGConfig) -> Embeddings:
    """Create an embedding model based on configuration.

    The returned ``Embeddings`` instance may also expose ``encode_sparse``
    and ``embed_with_sparse`` when the provider supports hybrid search.
    """
    provider = config.EMBEDDING_PROVIDER.lower()

    if provider == "qwen":
        return _create_qwen_embeddings(config)
    elif provider == "bge-m3":
        return _create_bge_m3_embeddings(config)
    elif provider == "openai":
        return _create_openai_embeddings(config)
    elif provider == "huggingface":
        return _create_huggingface_embeddings(config)
    else:
        raise ValueError(
            f"Unsupported embedding provider: {provider!r}. "
            f"Choose 'qwen', 'bge-m3', 'openai', or 'huggingface'."
        )


# ---------------------------------------------------------------------------
#  Qwen text-embedding-v4 — dense + sparse via DashScope
# ---------------------------------------------------------------------------


class QwenEmbeddings(Embeddings):
    """Qwen text-embedding-v4 via DashScope — **dense + sparse** hybrid vectors.

    Uses ``dashscope.TextEmbedding`` (NOT OpenAI-compatible API) because
    sparse output is only available through the native DashScope SDK.

    Key parameters:
    * **dimension**: 1024 (default), 2048, 1536, 768, 512, 256, 128, 64
    * **output_type**: ``"dense&sparse"`` for hybrid, ``"dense"`` for semantic-only
    * **text_type**: ``"query"`` for query-side encoding (better search perf),
      ``"document"`` for index-side encoding
    """

    def __init__(
        self,
        model: str = "text-embedding-v4",
        api_key: str = "",
        dimension: int = 1024,
        base_url: str = "",
    ) -> None:
        self._model = model
        self._dimension = dimension
        # Set API key via environment variable (most reliable for DashScope SDK).
        # The SDK reads DASHSCOPE_API_KEY at call time, so set it eagerly.
        key = api_key or ""
        if key:
            os.environ["DASHSCOPE_API_KEY"] = key
        # Allow overriding the DashScope base HTTP URL
        if base_url:
            import dashscope
            dashscope.base_http_api_url = base_url

    # ── LangChain Embeddings interface (dense only) ─────────────────

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batch-encode dense vectors for documents (index side)."""
        return self._encode(texts, output_type="dense", text_type="document")

    def embed_query(self, text: str) -> list[float]:
        """Encode a single query → dense vector (search side)."""
        result = self._encode([text], output_type="dense", text_type="query")
        return result[0]

    # ── Sparse / hybrid methods ─────────────────────────────────────

    def encode_sparse(
        self, texts: list[str], text_type: str = "document",
    ) -> list[dict[int, float]]:
        """Return sparse lexical weights for each text.

        Each dict maps **token_index** (int) → **weight** (float).
        This is text-embedding-v4's learned sparse representation —
        equivalent to neural BM25 for exact keyword matching.

        Args:
            texts: Texts to encode.
            text_type: ``"query"`` for search queries (directional),
                ``"document"`` for indexed content (default).
        """
        return self._encode_sparse_only(texts, text_type=text_type)

    def embed_with_sparse(
        self, texts: list[str],
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        """Encode texts → **both** dense and sparse vectors in a single call.

        Uses ``output_type="dense&sparse"`` — same cost as single-vector mode.
        """
        return self._encode_hybrid(texts, text_type="document")

    # ── Internal ────────────────────────────────────────────────────

    def _encode(
        self, texts: list[str], output_type: str, text_type: str,
    ) -> list[list[float]]:
        """Call DashScope and return dense vectors only."""
        # DashScope SDK reads DASHSCOPE_API_KEY at call time — re-assert
        # in case the env var was cleared between __init__ and this call.
        import dashscope
        from http import HTTPStatus

        kwargs: dict = {
            "model": self._model,
            "input": texts,
            "dimension": self._dimension,
            "text_type": text_type,
        }
        if output_type != "dense":
            kwargs["output_type"] = output_type

        resp = dashscope.TextEmbedding.call(**kwargs)

        if resp.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"DashScope embedding failed: status={resp.status_code} "
                f"code={resp.code} message={resp.message}"
            )

        return [
            item["embedding"]
            for item in resp.output["embeddings"]
        ]

    def _encode_sparse_only(
        self, texts: list[str], text_type: str = "document",
    ) -> list[dict[int, float]]:
        """Call DashScope with output_type='sparse'."""
        import dashscope
        from http import HTTPStatus

        resp = dashscope.TextEmbedding.call(
            model=self._model,
            input=texts,
            dimension=self._dimension,
            output_type="sparse",
            text_type=text_type,
        )

        if resp.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"DashScope sparse embedding failed: status={resp.status_code} "
                f"code={resp.code} message={resp.message}"
            )

        return [
            _parse_sparse(item.get("sparse_embedding", []))
            for item in resp.output["embeddings"]
        ]

    def _encode_hybrid(
        self, texts: list[str], text_type: str,
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        """Call DashScope with output_type='dense&sparse' — single API call."""
        import dashscope
        from http import HTTPStatus

        resp = dashscope.TextEmbedding.call(
            model=self._model,
            input=texts,
            dimension=self._dimension,
            output_type="dense&sparse",
            text_type=text_type,
        )

        if resp.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"DashScope hybrid embedding failed: status={resp.status_code} "
                f"code={resp.code} message={resp.message}"
            )

        dense_list: list[list[float]] = []
        sparse_list: list[dict[int, float]] = []

        for item in resp.output["embeddings"]:
            dense_list.append(item["embedding"])
            sparse_list.append(
                _parse_sparse(item.get("sparse_embedding", []))
            )

        return dense_list, sparse_list


def _parse_sparse(raw: list[dict]) -> dict[int, float]:
    """Convert DashScope sparse response to ``{index: weight}`` dict.

    DashScope returns: ``[{"index": 7149, "value": 0.829, "token": "风"}, ...]``
    Qdrant expects:   ``{7149: 0.829, ...}``
    """
    return {int(item["index"]): float(item["value"]) for item in raw}


def _create_qwen_embeddings(config: RAGConfig) -> QwenEmbeddings:
    """Create Qwen text-embedding-v4 embeddings (dense + sparse via DashScope)."""
    dimension = getattr(config, "EMBEDDING_DIMENSION", None) or 1024
    # Only use EMBEDDING_BASE_URL if explicitly set (don't fall back to GLM_BASE_URL).
    base_url = config.EMBEDDING_BASE_URL or ""

    logger.info(
        "Using Qwen embeddings: model=%s dim=%d",
        config.EMBEDDING_MODEL, dimension,
    )

    return QwenEmbeddings(
        model=config.EMBEDDING_MODEL or "text-embedding-v4",
        api_key=config.get_effective_api_key(),
        dimension=dimension,
        base_url=base_url,
    )


# ---------------------------------------------------------------------------
#  BGE-M3 — local dense + sparse  (FlagEmbedding)  [kept as fallback]
# ---------------------------------------------------------------------------


class BgeM3Embeddings(Embeddings):
    """BGE-M3 local embeddings producing **dense** and **sparse** vectors.

    Wraps ``FlagEmbedding.BGEM3FlagModel``.  The model runs locally (no API
    calls) and outputs:

    * **dense**   — 1024-dim float vector (standard semantic embedding)
    * **sparse**  — ``{token_id: weight}`` learned lexical weights

    Requires: ``pip install FlagEmbedding``
    """

    DENSE_DIM = 1024

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        use_fp16: bool = True,
        device: str | None = None,
    ) -> None:
        self._model_name = model_name
        self._use_fp16 = use_fp16
        self._device = device
        self._model: object | None = None

    @property
    def _m(self):  # type: ignore[override]
        if self._model is None:
            from FlagEmbedding import BGEM3FlagModel

            logger.info(
                "Loading BGE-M3 model: %s (fp16=%s, device=%s)",
                self._model_name, self._use_fp16, self._device or "auto",
            )
            self._model = BGEM3FlagModel(
                self._model_name,
                use_fp16=self._use_fp16,
                device=self._device,
            )
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        output = self._m.encode(
            texts, batch_size=12, max_length=8192,
            return_dense=True, return_sparse=False,
        )
        return _to_list(output["dense_vecs"])

    def embed_query(self, text: str) -> list[float]:
        output = self._m.encode(
            [text], max_length=8192,
            return_dense=True, return_sparse=False,
        )
        return _to_list(output["dense_vecs"])[0]

    def encode_sparse(self, texts: list[str]) -> list[dict[int, float]]:
        output = self._m.encode(
            texts, batch_size=12, max_length=8192,
            return_dense=False, return_sparse=True,
        )
        return output["lexical_weights"]

    def embed_with_sparse(
        self, texts: list[str],
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        output = self._m.encode(
            texts, batch_size=12, max_length=8192,
            return_dense=True, return_sparse=True,
        )
        return _to_list(output["dense_vecs"]), output["lexical_weights"]


def _to_list(ndarray: np.ndarray) -> list[list[float]]:
    import numpy as np

    arr = np.asarray(ndarray)
    if arr.ndim == 1:
        return [arr.tolist()]
    return arr.tolist()


# ---------------------------------------------------------------------------
#  OpenAI-compatible API embeddings
# ---------------------------------------------------------------------------


def _create_openai_embeddings(config: RAGConfig) -> Embeddings:
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


# ---------------------------------------------------------------------------
#  HuggingFace local embeddings (sentence-transformers)
# ---------------------------------------------------------------------------


def _create_huggingface_embeddings(config: RAGConfig) -> Embeddings:
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
