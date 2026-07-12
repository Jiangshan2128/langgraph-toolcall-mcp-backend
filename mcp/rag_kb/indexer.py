"""Document indexer — manages the full indexing lifecycle.

On startup, the indexer:
    1. Scans the documents directory for all supported files.
    2. Computes content hashes (MD5) to detect changes.
    3. Adds new/changed files; deletes stale sources.
    4. Maintains a hash index (``.index_cache.json``) for incremental runs.

The indexer is **idempotent** — running it multiple times only processes
files that have changed since the last run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from rag_kb.config import RAGConfig
from rag_kb.interfaces import IndexResult, IndexedDoc, VectorStoreInterface
from rag_kb.loader import load_directory, load_file
from rag_kb.splitter import split_documents

logger = logging.getLogger(__name__)

# Name of the cache file stored inside the documents directory
_CACHE_FILE = ".index_cache.json"


def _file_hash(file_path: Path) -> str:
    """Compute MD5 hash of a file's contents."""
    h = hashlib.md5()
    h.update(file_path.read_bytes())
    return h.hexdigest()


def _load_cache(doc_dir: Path) -> dict[str, str]:
    """Load the index cache from disk."""
    cache_path = doc_dir / _CACHE_FILE
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            logger.debug("Loaded index cache with %d entries", len(data))
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load index cache: %s; rebuilding", e)
    return {}


def _save_cache(doc_dir: Path, cache: dict[str, str]) -> None:
    """Persist the index cache to disk."""
    cache_path = doc_dir / _CACHE_FILE
    try:
        cache_path.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.debug("Saved index cache with %d entries", len(cache))
    except OSError as e:
        logger.warning("Failed to save index cache: %s", e)


def _collect_documents(doc_dir: Path) -> dict[str, Path]:
    """Walk the documents directory and return {rel_path: abs_path}.

    Internal cache files (``.index_cache.json``) are automatically excluded.
    """
    supported_exts = {".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml",
                      ".yml", ".toml", ".cfg", ".ini", ".log", ".csv",
                      ".pdf", ".html", ".htm", ".docx"}

    # Names/paths to exclude from indexing
    exclude_names = {_CACHE_FILE}

    # Prefixes to exclude (e.g. Office temp files like ~$document.docx)
    exclude_prefixes = ("~$", "~")

    files: dict[str, Path] = {}
    if not doc_dir.is_dir():
        logger.warning("Documents directory does not exist: %s", doc_dir)
        return files

    for file_path in sorted(doc_dir.rglob("*")):
        name = file_path.name
        if (
            file_path.is_file()
            and file_path.suffix.lower() in supported_exts
            and name not in exclude_names
            and not name.startswith(exclude_prefixes)
        ):
            rel = str(file_path.relative_to(doc_dir))
            files[rel] = file_path

    return files


def index_documents(
    store: VectorStoreInterface,
    config: RAGConfig,
    full_rebuild: bool = False,
) -> IndexResult:
    """Index all documents from the configured documents directory.

    This is the **primary indexing entry point**.  Call it on server startup
    and on ``refresh_index``.

    Args:
        store: The vector store backend.
        config: RAG configuration.
        full_rebuild: If True, re-index every file (ignore cache).

    Returns:
        An ``IndexResult`` with a summary of what happened.
    """
    doc_dir = Path(config.DOCUMENTS_PATH)
    result = IndexResult()

    if not doc_dir.is_dir():
        logger.info("Documents directory does not exist, creating: %s", doc_dir)
        doc_dir.mkdir(parents=True, exist_ok=True)
        return result

    # Collect current files and load cache
    current_files = _collect_documents(doc_dir)
    logger.info("Found %d files in documents directory", len(current_files))
    old_cache = {} if full_rebuild else _load_cache(doc_dir)
    new_cache: dict[str, str] = {}
    changed: list[Path] = []

    # Detect new / changed files
    for rel, abs_path in current_files.items():
        result.total_files += 1
        h = _file_hash(abs_path)
        new_cache[rel] = h

        if full_rebuild:
            changed.append(abs_path)
            result.indexed_files += 1
        elif rel not in old_cache:
            changed.append(abs_path)
            result.indexed_files += 1
        elif old_cache[rel] != h:
            changed.append(abs_path)
            result.indexed_files += 1
        else:
            result.skipped_files += 1

    # Detect deleted files — remove from store
    deleted_sources: list[str] = []
    if not full_rebuild:
        for rel in old_cache:
            if rel not in current_files:
                deleted_sources.append(str(doc_dir / rel))
                result.deleted_files += 1

    # Remove stale sources
    for source_path in deleted_sources:
        try:
            store.delete_by_source(source_path)
        except Exception as e:
            logger.warning("Failed to delete stale source '%s': %s", source_path, e)
            result.errors.append(f"delete {source_path}: {e}")

    # Index changed files
    for file_path in changed:
        try:
            # Load, split, index
            docs = load_file(str(file_path))
            chunks = split_documents(docs, config)
            indexed = [
                IndexedDoc(
                    content=chunk.page_content,
                    metadata=chunk.metadata,
                )
                for chunk in chunks
            ]
            count = store.add_documents(indexed)
            result.total_chunks += count
        except Exception as e:
            logger.warning("Failed to index '%s': %s", file_path, e)
            result.errors.append(f"index {file_path.name}: {e}")

    # Persist cache
    _save_cache(doc_dir, new_cache)

    logger.info(
        "Indexing complete: %s",
        result.summary,
    )
    return result
