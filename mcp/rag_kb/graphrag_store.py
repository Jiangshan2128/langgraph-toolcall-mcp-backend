"""GraphRAG store — loads graphrag index output and queries via GraphRAG's built-in search.

Lifecycle::

    store = GraphRagStore("mcp/graphrag")
    result = await store.search_local("这份文档涉及哪些技术领域")
    result = await store.search_global("文档整体讲了什么")
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ── Parquet file names ───────────────────────────────────────────────
_PARQUET_ENTITIES = "entities.parquet"
_PARQUET_RELATIONSHIPS = "relationships.parquet"
_PARQUET_COMMUNITIES = "communities.parquet"
_PARQUET_COMMUNITY_REPORTS = "community_reports.parquet"
_PARQUET_TEXT_UNITS = "text_units.parquet"
_PARQUET_DOCUMENTS = "documents.parquet"


@dataclass
class GraphRagStats:
    """Statistics about a graphrag knowledge graph."""

    entity_count: int = 0
    relationship_count: int = 0
    community_count: int = 0
    text_unit_count: int = 0
    document_count: int = 0
    is_ready: bool = False


class GraphRagStore:
    """Query a graphrag knowledge graph using GraphRAG's built-in search engine.

    Loads parquet files and ``settings.yaml`` from the graphrag project
    directory, then delegates to ``graphrag.api.local_search`` /
    ``graphrag.api.global_search`` for retrieval-augmented generation.

    Args:
        root_dir: Path to the graphrag project directory.
    """

    def __init__(self, root_dir: str) -> None:
        self._root_dir = Path(root_dir).resolve()
        self._loaded = False

        # DataFrames (populated on first load)
        self._entities: pd.DataFrame | None = None
        self._relationships: pd.DataFrame | None = None
        self._communities: pd.DataFrame | None = None
        self._community_reports: pd.DataFrame | None = None
        self._text_units: pd.DataFrame | None = None
        self._documents: pd.DataFrame | None = None

    # ── Lifecycle ────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        """True if the graphrag index output exists."""
        output_dir = self._root_dir / "output"
        return (output_dir / _PARQUET_ENTITIES).exists()

    def get_stats(self) -> GraphRagStats:
        """Return statistics about the knowledge graph."""
        self._ensure_loaded()
        return GraphRagStats(
            entity_count=len(self._entities) if self._entities is not None else 0,
            relationship_count=len(self._relationships) if self._relationships is not None else 0,
            community_count=len(self._communities) if self._communities is not None else 0,
            text_unit_count=len(self._text_units) if self._text_units is not None else 0,
            document_count=len(self._documents) if self._documents is not None else 0,
            is_ready=self._loaded,
        )

    # ── Search ──────────────────────────────────────────────────────

    async def search_local(
        self,
        query: str,
        community_level: int = 1,
        response_type: str = "multiple paragraphs",
    ) -> str:
        """Local search — entity/relationship focused.

        Best for specific questions about entities, parameters, and
        relationships (e.g. "机芯供电电压是多少").

        Args:
            query: Natural language query.
            community_level: Which hierarchy level to search (0=most granular).
            response_type: Style of response.

        Returns:
            Answer text.
        """
        return await self._search(
            mode="local",
            query=query,
            community_level=community_level,
            response_type=response_type,
        )

    async def search_global(
        self,
        query: str,
        community_level: int = 1,
        response_type: str = "multiple paragraphs",
    ) -> str:
        """Global search — community/theme focused.

        Best for big-picture questions, cross-document synthesis,
        and summarization (e.g. "三个文档的共同主题是什么").

        Args:
            query: Natural language query.
            community_level: Which hierarchy level to search (higher = broader).
            response_type: Style of response.

        Returns:
            Answer text.
        """
        return await self._search(
            mode="global",
            query=query,
            community_level=community_level,
            response_type=response_type,
        )

    # ── Internal ────────────────────────────────────────────────────

    async def _search(
        self,
        mode: str,
        query: str,
        community_level: int = 1,
        response_type: str = "multiple paragraphs",
    ) -> str:
        """Run GraphRAG search via its query API."""
        self._ensure_loaded()
        if not self._loaded:
            return (
                "[GraphRAG 索引未就绪] 知识图谱尚未构建。\n\n"
                "请先运行以下命令构建索引：\n"
                f"  graphrag index --root {self._root_dir}\n\n"
                "在此之前，建议使用 search_docs 进行向量检索。"
            )

        try:
            from graphrag.config.load_config import load_config

            config = load_config(root_dir=str(self._root_dir))

            if mode == "local":
                from graphrag.api import local_search

                answer, context = await local_search(
                    config=config,
                    entities=self._entities,
                    communities=self._communities,
                    community_reports=self._community_reports,
                    text_units=self._text_units,
                    relationships=self._relationships,
                    covariates=None,
                    community_level=community_level,
                    response_type=response_type,
                    query=query,
                )
            else:
                from graphrag.api import global_search

                answer, context = await global_search(
                    config=config,
                    entities=self._entities,
                    communities=self._communities,
                    community_reports=self._community_reports,
                    community_level=community_level,
                    dynamic_community_selection=False,
                    response_type=response_type,
                    query=query,
                )

            return self._strip_citations(str(answer)) if answer else "[GraphRAG 未返回结果]"

        except Exception as e:
            logger.error("GraphRAG %s search failed: %s", mode, e, exc_info=True)
            return (
                f"[GraphRAG {mode} 查询失败] {e}\n\n"
                "建议改用 search_docs 进行向量检索。"
            )

    @staticmethod
    def _strip_citations(text: str) -> str:
        """Remove [Data: ...] citation markers from the answer text.

        GraphRAG's prompt instructs the LLM to include grounding references
        like ``[Data: Entities (1, 2); Sources (3)]``.  These are useful for
        debugging but clutter user-facing output.
        """
        import re
        return re.sub(r"\s*\[Data:[^\]]*\]", "", text)

    # ── Internal: data loading ───────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Lazy-load parquet data on first access."""
        if self._loaded or not self.is_ready:
            return

        # Load each parquet independently — one failure won't cascade
        for attr, name in [
            ("_entities", _PARQUET_ENTITIES),
            ("_relationships", _PARQUET_RELATIONSHIPS),
            ("_communities", _PARQUET_COMMUNITIES),
            ("_community_reports", _PARQUET_COMMUNITY_REPORTS),
            ("_text_units", _PARQUET_TEXT_UNITS),
            ("_documents", _PARQUET_DOCUMENTS),
        ]:
            try:
                setattr(self, attr, self._read_parquet(name))
            except Exception as e:
                logger.warning("Failed to load %s: %s", name, e)

        # Mark loaded if we got at least the core data
        self._loaded = self._entities is not None

        if self._loaded:
            logger.info(
                "GraphRagStore loaded: %d entities, %d relationships, "
                "%d communities, %d reports, %d text_units",
                len(self._entities) if self._entities is not None else 0,
                len(self._relationships) if self._relationships is not None else 0,
                len(self._communities) if self._communities is not None else 0,
                len(self._community_reports) if self._community_reports is not None else 0,
                len(self._text_units) if self._text_units is not None else 0,
            )
        else:
            logger.warning("GraphRAG data incomplete — entities.parquet not found")

    def _read_parquet(self, name: str) -> pd.DataFrame | None:
        """Read a parquet file from the output directory."""
        path = self._root_dir / "output" / name
        if not path.exists():
            return None
        return pd.read_parquet(path)
