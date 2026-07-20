#!/usr/bin/env python3
"""MRR (Mean Reciprocal Rank) evaluation for the RAG knowledge base.

Measures how early the first relevant chunk appears in search results.
For technical document retrieval (one section = one correct answer), MRR
is more meaningful than NDCG because LLM consumers need the *specific*
section, not just any section from the same document.

Usage::

    cd backend
    python mcp/eval_mrr.py                          # default: 10 queries, top_k=10
    python mcp/eval_mrr.py -t custom.json -k 20     # custom test file, top_k=20
    python mcp/eval_mrr.py -o report.txt            # save report to file

The MCP server must NOT be running during eval — Qdrant local mode allows
only one client per data directory.

Ground truth format
-------------------
Relevance is determined by **heading metadata** (not chunk_index, which
changes on re-index).  Each query in the JSON file specifies heading
criteria the correct chunk must satisfy::

    {
      "query": "机芯供电电压",
      "relevant": {"h2": "2.2 供电规格"},
      "file_name": "diaocang",        // optional: substring match
      "description": "供电规格章节"    // optional: shown in report
    }

All keys in ``relevant`` must match (AND semantics).  ``file_name`` uses
case-insensitive substring containment against the chunk's source filename.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# ── sys.path setup (mirrors server.py & print_chunks.py) ─────────────
_mcp_dir = str(Path(__file__).resolve().parent)
if _mcp_dir not in sys.path:
    sys.path.insert(0, _mcp_dir)

from rag_kb.config import get_rag_config
from rag_kb.interfaces import SearchResult
from rag_kb.retriever import RAGRetriever

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("eval_mrr")

# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class PerQueryResult:
    """Result for a single evaluation query."""

    index: int
    query: str
    rank: int | None  # 1-based, None = not found
    rr: float  # reciprocal rank (0.0 if not found)
    file_name: str  # which document matched (or "—")
    description: str
    matched_chunk: int | None  # 0-based chunk_index of the match


@dataclass
class EvalResult:
    """Aggregated MRR evaluation result."""

    test_file: str
    top_k: int
    total_chunks: int
    per_query: list[PerQueryResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.per_query)

    @property
    def hits(self) -> int:
        return sum(1 for q in self.per_query if q.rank is not None)

    @property
    def mrr(self) -> float:
        if not self.per_query:
            return 0.0
        return sum(q.rr for q in self.per_query) / len(self.per_query)

    @property
    def mean_rank(self) -> float:
        ranks = [q.rank for q in self.per_query if q.rank is not None]
        if not ranks:
            return 0.0
        return sum(ranks) / len(ranks)

    def hits_at(self, k: int) -> tuple[int, int]:
        """Return (count, total) for queries where rank ≤ k."""
        count = sum(1 for q in self.per_query if q.rank is not None and q.rank <= k)
        return count, self.total


# ── Query loading & validation ───────────────────────────────────────


def load_queries(path: str) -> list[dict[str, Any]]:
    """Load and validate test queries from a JSON file.

    Raises:
        SystemExit: If the file is missing, malformed, or has invalid entries.
    """
    file_path = Path(path)
    if not file_path.exists():
        logger.error("Test file not found: %s", file_path)
        sys.exit(1)

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in %s: %s", file_path, e)
        sys.exit(1)

    if not isinstance(data, list):
        logger.error("Test file must contain a JSON array, got %s", type(data).__name__)
        sys.exit(1)

    queries: list[dict[str, Any]] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            logger.error("Entry %d is not a JSON object, skipping", i)
            continue

        query = entry.get("query", "").strip()
        relevant = entry.get("relevant", {})

        if not query:
            logger.error("Entry %d: missing or empty 'query' field, skipping", i)
            continue
        if not isinstance(relevant, dict) or not relevant:
            logger.error("Entry %d: missing or empty 'relevant' object, skipping", i)
            continue

        # Validate relevant keys
        valid_keys = {"h1", "h2", "h3", "h4", "file_name"}
        unknown = set(relevant.keys()) - valid_keys
        if unknown:
            logger.warning(
                "Entry %d: unknown keys in 'relevant' %s (valid: %s)",
                i, unknown, sorted(valid_keys),
            )

        # Ensure heading values are strings
        for key in ("h1", "h2", "h3", "h4"):
            if key in relevant and not isinstance(relevant[key], str):
                logger.error(
                    "Entry %d: '%s' value must be a string, got %s",
                    i, key, type(relevant[key]).__name__,
                )
                continue

        queries.append({
            "query": query,
            "relevant": relevant,
            "file_name": entry.get("file_name", "").strip(),
            "description": entry.get("description", "").strip(),
        })

    if not queries:
        logger.error("No valid queries found in %s", file_path)
        sys.exit(1)

    return queries


# ── Chunk matching ───────────────────────────────────────────────────


def chunk_matches_criteria(metadata: dict, criteria: dict) -> bool:
    """Return True if the chunk matches all specified heading criteria.

    Args:
        metadata: Chunk metadata from ``SearchResult.metadata``.
                  Contains ``h1``-``h4``, ``file_name``, ``chunk_index``, etc.
        criteria: Heading criteria from the test query's ``"relevant"`` field.
                  Keys: ``h1``, ``h2``, ``h3``, ``h4``, ``file_name``.

    Matching rules:
        - ``h1``-``h4``: exact string match.
        - ``file_name``: case-insensitive substring containment
          (e.g. ``"diaocang"`` matches ``"diaocang.docx"``).
        - If ``file_name`` is in criteria, it takes priority —
          the chunk is only considered if its file_name matches first,
          then heading criteria are checked.
    """
    if not criteria:
        return False  # empty criteria → ambiguous, treat as no match

    # file_name filtering (applied first to narrow scope)
    if "file_name" in criteria:
        expected_fn = criteria["file_name"].lower()
        actual_fn = metadata.get("file_name", "").lower()
        if expected_fn not in actual_fn:
            return False

    # Heading matching (all specified keys must match exactly)
    for key in ("h1", "h2", "h3", "h4"):
        if key in criteria:
            actual = metadata.get(key, "")
            if actual != criteria[key]:
                return False

    return True


def find_rank(results: list[SearchResult], criteria: dict) -> tuple[int | None, int | None]:
    """Return (1-based rank, chunk_index) of the first matching result.

    Args:
        results: Ranked search results from ``similarity_search()``.
        criteria: Heading criteria for matching.

    Returns:
        Tuple of ``(rank, chunk_index)``.  ``rank`` is None if no match.
        ``chunk_index`` is the 0-based index from the chunk's metadata.
    """
    for rank, result in enumerate(results, start=1):
        if chunk_matches_criteria(result.metadata, criteria):
            chunk_idx = result.metadata.get("chunk_index")
            return rank, chunk_idx
    return None, None


# ── Main evaluation loop ─────────────────────────────────────────────


def compute_mrr(
    queries: list[dict[str, Any]],
    retriever: RAGRetriever,
    top_k: int,
) -> EvalResult:
    """Run the MRR evaluation loop over a set of test queries.

    For each query:
        1. Call ``retriever._store.similarity_search(query, k=top_k)``.
        2. Find the rank of the first chunk matching the heading criteria.
        3. Compute reciprocal rank (RR = 1/rank, or 0.0 if not found).

    Prints progress to stderr as each query is processed.
    """
    store = retriever._store
    results: list[PerQueryResult] = []
    total = len(queries)

    for i, q in enumerate(queries):
        query_text = q["query"]
        criteria = q["relevant"]
        description = q.get("description", "")

        # If file_name filter is provided, merge it into criteria
        if q.get("file_name"):
            criteria = {**criteria, "file_name": q["file_name"]}

        # Search
        hits = store.similarity_search(query_text, k=top_k)

        # Match
        rank, chunk_idx = find_rank(hits, criteria)

        # Reciprocal Rank
        rr = 1.0 / rank if rank is not None else 0.0
        matched_file = (
            hits[rank - 1].file_name if rank is not None else "—"
        )

        per = PerQueryResult(
            index=i + 1,
            query=query_text,
            rank=rank,
            rr=rr,
            file_name=matched_file,
            description=description,
            matched_chunk=chunk_idx,
        )

        results.append(per)

        # Progress
        status = f"rank={rank}" if rank else "NOT FOUND"
        logger.info(
            "  [%d/%d] %s — %s",
            i + 1, total, status, query_text[:60],
        )

    return EvalResult(
        test_file="",  # set by caller
        top_k=top_k,
        total_chunks=store.get_document_count(),
        per_query=results,
    )


# ── Report formatting ────────────────────────────────────────────────


def format_report(result: EvalResult) -> str:
    """Build a human-readable MRR evaluation report string."""
    lines: list[str] = []

    # Header
    lines.append("=" * 60)
    lines.append("  MRR Evaluation Report")
    lines.append(f"  Test file: {result.test_file}")
    lines.append(f"  Top-K: {result.top_k}")
    lines.append(f"  Indexed chunks: {result.total_chunks}")
    lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append("")

    # Overall
    lines.append(f"Queries: {result.total} | MRR: {result.mrr:.4f}")
    if result.hits > 0:
        lines.append(f"Hits: {result.hits}/{result.total} | Mean Rank (hits): {result.mean_rank:.2f}")
    lines.append("")

    # Per-query table
    lines.append("Per-Query Breakdown:")
    header = f"  {'#':>3}  {'Query':<35} {'Rank':>5}  {'RR':>7}  {'Chunk':>5}  {'File'}"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    for q in result.per_query:
        rank_str = str(q.rank) if q.rank is not None else "--"
        chunk_str = str(q.matched_chunk) if q.matched_chunk is not None else "--"
        flag = "  *" if (q.rank is not None and q.rank > 3) else ""

        # Truncate query for table alignment
        query_display = q.query[:32] + "..." if len(q.query) > 35 else q.query

        lines.append(
            f"  {q.index:>3}  {query_display:<35} {rank_str:>5}  {q.rr:>7.4f}  "
            f"{chunk_str:>5}  {q.file_name}{flag}"
        )

    if any(q.rank is not None and q.rank > 3 for q in result.per_query):
        lines.append("")
        lines.append("  * = correct answer ranks below top 3")

    lines.append("")

    # Summary statistics
    lines.append("Summary Statistics:")
    for k in (1, 3, 5, 10):
        count, total = result.hits_at(k)
        lines.append(f"  Hits@{k:>2}:  {count:>3}/{total} ({100*count/total:.1f}%)")

    not_found = result.total - result.hits
    lines.append(f"  Not found: {not_found}/{result.total} ({100*not_found/result.total:.1f}%)")
    lines.append("  " + "─" * 37)
    lines.append(f"  MRR:                 {result.mrr:.4f}")
    if result.hits > 0:
        lines.append(f"  Mean Rank (hits):    {result.mean_rank:.2f}")

    lines.append("")
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MRR evaluation for RAG knowledge base",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mcp/eval_mrr.py
  python mcp/eval_mrr.py -t custom.json -k 20
  python mcp/eval_mrr.py -o report.txt
        """,
    )
    parser.add_argument(
        "-t", "--test-file",
        type=str,
        default=None,
        help="Path to test queries JSON (default: mcp/eval_mrr_queries.json)",
    )
    parser.add_argument(
        "-k", "--top-k",
        type=int,
        default=10,
        help="Number of results to retrieve per query (default: 10)",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Write report to file instead of stdout",
    )

    args = parser.parse_args()

    # Resolve test file path
    test_file = args.test_file
    if test_file is None:
        test_file = str(Path(__file__).with_name("eval_mrr_queries.json"))

    # Load queries
    queries = load_queries(test_file)
    logger.info(
        "Loaded %d test query(s) from %s (top_k=%d)",
        len(queries), test_file, args.top_k,
    )

    # Initialize retriever (standalone — no FastMCP dependency)
    logger.info("Initializing RAGRetriever...")
    config = get_rag_config()
    retriever = RAGRetriever(config)
    retriever.initialize()
    logger.info("Retriever ready; running evaluation...")

    # Evaluate
    result = compute_mrr(queries, retriever, args.top_k)
    result.test_file = test_file

    # Cleanup
    retriever.shutdown()

    # Format & output
    report = format_report(result)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(report, encoding="utf-8")
        print(f"Report saved to: {output_path.absolute()}")
    else:
        print(report)

    # Summary log
    logger.info(
        "MRR: %.4f | Hits@1: %d/%d | Hits@3: %d/%d | Hits@5: %d/%d",
        result.mrr,
        *result.hits_at(1),
        *result.hits_at(3),
        *result.hits_at(5),
    )


if __name__ == "__main__":
    main()
