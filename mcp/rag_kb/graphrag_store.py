"""GraphRAG store — loads graphrag index output and queries via direct LLM.

This module does NOT use graphrag's query engine (which requires
community_reports.parquet and a LanceDB vector store that v3.1.0
cannot produce reliably).  Instead it:

1. Loads entities, relationships, and text_units from parquet files.
2. Extracts relevant entities and their relationships for a query.
3. Constructs a knowledge-graph-aware prompt.
4. Calls DeepSeek directly (via OpenAI-compatible API).

Lifecycle::

    store = GraphRagStore("mcp/graphrag")
    result = store.search("这份文档涉及哪些技术领域")
"""

from __future__ import annotations

import json
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
    """Query a graphrag knowledge graph via direct LLM calls.

    Loads entities, relationships, and text_units from graphrag's parquet
    output, then constructs prompts that include:
    - Top related entities and their descriptions
    - Relationships between those entities
    - Relevant text units for context

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
        self._text_units: pd.DataFrame | None = None
        self._documents: pd.DataFrame | None = None

        # LLM config (loaded from env for simplicity)
        self._llm_api_key = ""
        self._llm_base_url = ""
        self._llm_model = ""

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

    def search(self, query: str) -> str:
        """Search the knowledge graph via entity extraction + direct LLM call.

        Args:
            query: Natural language query.

        Returns:
            LLM answer informed by the knowledge graph.
        """
        self._ensure_loaded()
        if not self._loaded:
            return (
                "[GraphRAG 索引未就绪] 知识图谱尚未构建。\n\n"
                "请先运行以下命令构建索引：\n"
                f"  graphrag index --root {self._root_dir}\n\n"
                "在此之前，建议使用 search_docs 进行向量检索。"
            )

        try:
            # Build knowledge-graph-informed context
            context = self._build_kg_context(query)
            answer = self._call_llm(query, context)
            return answer
        except Exception as e:
            logger.error("GraphRAG search failed: %s", e, exc_info=True)
            return (
                f"[GraphRAG 查询失败] {e}\n\n"
                "建议改用 search_docs 进行向量检索。"
            )

    # ── Internal: data loading ───────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Lazy-load parquet data on first access."""
        if self._loaded or not self.is_ready:
            return

        try:
            self._entities = self._read_parquet(_PARQUET_ENTITIES)
            self._relationships = self._read_parquet(_PARQUET_RELATIONSHIPS)
            self._communities = self._read_parquet(_PARQUET_COMMUNITIES)
            self._text_units = self._read_parquet(_PARQUET_TEXT_UNITS)
            self._documents = self._read_parquet(_PARQUET_DOCUMENTS)

            # Load LLM config from environment (same keys as settings.yaml)
            self._llm_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
            self._llm_base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            self._llm_model = "deepseek-chat"

            self._loaded = True
            logger.info(
                "GraphRagStore loaded: %d entities, %d relationships, "
                "%d communities, %d text_units",
                len(self._entities) if self._entities is not None else 0,
                len(self._relationships) if self._relationships is not None else 0,
                len(self._communities) if self._communities is not None else 0,
                len(self._text_units) if self._text_units is not None else 0,
            )
        except Exception as e:
            logger.error("Failed to load GraphRAG data: %s", e, exc_info=True)

        if self._text_units is None and self._entities is None:
            logger.warning("GraphRAG data incomplete")
            self._loaded = False

    def _read_parquet(self, name: str) -> pd.DataFrame | None:
        """Read a parquet file from the output directory."""
        path = self._root_dir / "output" / name
        if not path.exists():
            return None
        return pd.read_parquet(path)

    # ── Internal: KG context building ────────────────────────────────

    def _build_kg_context(self, query: str) -> str:
        """Build knowledge-graph context for the query.

        Strategy:
        1. Search entity names/descriptions for query keyword matches.
        2. Collect related entities via relationship edges.
        3. Collect text_units associated with matched entities.
        4. Format as a structured prompt context.
        """
        parts: list[str] = []

        # ── 1. Match entities ────────────────────────────────────────
        matched_entities = self._match_entities(query, top_n=20)
        if matched_entities is not None and len(matched_entities) > 0:
            parts.append("## 相关实体")
            for _, row in matched_entities.iterrows():
                name = row.get("name", row.get("title", ""))
                desc = row.get("description", "")
                etype = row.get("type", "")
                if not name:
                    continue
                line = f"- **{name}**"
                if etype:
                    line += f" [{etype}]"
                if desc:
                    desc_short = str(desc)[:200]
                    line += f": {desc_short}"
                parts.append(line)
            parts.append("")

        # ── 2. Match relationships ──────────────────────────────────
        if matched_entities is not None and len(matched_entities) > 0:
            entity_names = set()
            for _, row in matched_entities.iterrows():
                name = row.get("name", row.get("title", ""))
                if name:
                    entity_names.add(str(name))

            matched_rels = self._match_relationships(entity_names, top_n=30)
            if matched_rels is not None and len(matched_rels) > 0:
                parts.append("## 实体关系")
                for _, row in matched_rels.iterrows():
                    source = row.get("source", "")
                    target = row.get("target", "")
                    desc = row.get("description", "")
                    if not source or not target:
                        continue
                    line = f"- {source} → {target}"
                    if desc:
                        desc_short = str(desc)[:200]
                        line += f": {desc_short}"
                    parts.append(line)
                parts.append("")

        # ── 3. Match text_units ─────────────────────────────────────
        if matched_entities is not None and len(matched_entities) > 0:
            text_ids = set()
            for _, row in matched_entities.iterrows():
                tu_ids = row.get("text_unit_ids", row.get("text_unit_ids", ""))
                if isinstance(tu_ids, str):
                    try:
                        ids = json.loads(tu_ids)
                        text_ids.update(ids)
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif isinstance(tu_ids, (list,)):
                    text_ids.update(tu_ids)

            if text_ids and self._text_units is not None:
                matched_texts = self._match_text_units(text_ids, top_n=5)
                if matched_texts is not None and len(matched_texts) > 0:
                    parts.append("## 相关文档片段")
                    for i, (_, row) in enumerate(matched_texts.iterrows(), 1):
                        text = str(row.get("text", row.get("chunk", "")))[:800]
                        if text.strip():
                            parts.append(f"### 片段 {i}")
                            parts.append(text)
                            parts.append("")
                    parts.append("")

        if not parts:
            # Fallback: dump all entity names + the first few text_units
            parts.append("## 全部实体概览")
            if self._entities is not None:
                for _, row in self._entities.head(50).iterrows():
                    name = row.get("name", row.get("title", ""))
                    etype = row.get("type", "")
                    if name:
                        parts.append(f"- {name} [{etype}]")
            parts.append("")

            if self._text_units is not None:
                parts.append("## 文档内容")
                for i, (_, row) in enumerate(self._text_units.head(5).iterrows(), 1):
                    text = str(row.get("text", row.get("chunk", "")))[:800]
                    if text.strip():
                        parts.append(f"### 片段 {i}")
                        parts.append(text)
                        parts.append("")
            parts.append("")

        return "\n".join(parts)

    def _match_entities(self, query: str, top_n: int = 20) -> pd.DataFrame | None:
        """Find entities whose name or description matches the query."""
        if self._entities is None or len(self._entities) == 0:
            return None

        # Simple keyword matching on entity names
        query_terms = query.lower().split()
        name_col = next(
            (c for c in ["name", "title"] if c in self._entities.columns), None
        )
        if name_col is None:
            return self._entities.head(top_n)

        # Score: count query term matches in entity name + description
        def score(row):
            s = 0
            name = str(row.get(name_col, "")).lower()
            desc = str(row.get("description", "")).lower()
            for term in query_terms:
                if term in name:
                    s += 3
                elif term in desc:
                    s += 1
            return s

        entities = self._entities.copy()
        entities["_score"] = entities.apply(score, axis=1)
        return entities.sort_values("_score", ascending=False).head(top_n)

    def _match_relationships(self, entity_names: set[str], top_n: int = 30) -> pd.DataFrame | None:
        """Find relationships involving the given entities."""
        if self._relationships is None or len(self._relationships) == 0:
            return None

        source_col = next(
            (c for c in ["source", "subject"] if c in self._relationships.columns), None
        )
        target_col = next(
            (c for c in ["target", "object"] if c in self._relationships.columns), None
        )
        if source_col is None or target_col is None:
            return None

        rels = self._relationships[
            self._relationships[source_col].isin(entity_names)
            | self._relationships[target_col].isin(entity_names)
        ]
        return rels.head(top_n)

    def _match_text_units(self, text_ids: set[str], top_n: int = 5) -> pd.DataFrame | None:
        """Get text units by IDs."""
        if self._text_units is None:
            return None

        id_col = next(
            (c for c in ["id", "human_readable_id"] if c in self._text_units.columns), None
        )
        if id_col is None:
            return self._text_units.head(top_n)

        matched = self._text_units[self._text_units[id_col].astype(str).isin(
            set(str(x) for x in text_ids)
        )]
        return matched.head(top_n) if len(matched) > 0 else self._text_units.head(top_n)

    # ── Internal: LLM call ──────────────────────────────────────────

    def _call_llm(self, query: str, context: str) -> str:
        """Call DeepSeek (OpenAI-compatible) with KG context."""
        from openai import OpenAI

        client = OpenAI(
            api_key=self._llm_api_key,
            base_url=self._llm_base_url,
        )

        system_prompt = (
            "你是一个技术文档分析助手。下面是知识图谱中有关于提问的实体信息、关系信息和文档片段，"
            "请基于这些结构化信息回答问题。如果信息不足以回答问题，请诚实说明。"
        )

        user_prompt = (
            f"## 用户问题\n{query}\n\n"
            f"{context}\n\n"
            "请基于以上知识图谱信息回答问题。"
        )

        response = client.chat.completions.create(
            model=self._llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=1500,
        )

        return response.choices[0].message.content or ""
