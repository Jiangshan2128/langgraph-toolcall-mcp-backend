"""File system watcher — monitors the documents directory for changes.

When a file is added, modified, or deleted, the watcher triggers an
incremental re-index of just that file.

Uses ``watchdog`` (cross-platform file system notification library).
If ``watchdog`` is not installed, the watcher logs a warning and is a no-op.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag_kb.interfaces import VectorStoreInterface
    from rag_kb.config import RAGConfig

logger = logging.getLogger(__name__)

_observer = None


def _can_use_watchdog() -> bool:
    """Check if watchdog is available."""
    try:
        import watchdog  # noqa: F401
        return True
    except ImportError:
        return False


def start_watcher(store: VectorStoreInterface, config: RAGConfig) -> None:
    """Start a background file watcher on the documents directory.

    Args:
        store: Vector store backend for incremental re-indexing.
        config: RAG configuration (uses ``DOCUMENTS_PATH``).
    """
    if not _can_use_watchdog():
        logger.warning(
            "watchdog is not installed. File watching is disabled. "
            "Install: pip install watchdog"
        )
        return

    global _observer
    if _observer is not None:
        logger.info("File watcher is already running.")
        return

    import watchdog.events
    import watchdog.observers

    doc_dir = Path(config.DOCUMENTS_PATH)
    if not doc_dir.is_dir():
        logger.warning(
            "Documents directory does not exist, not starting watcher: %s",
            doc_dir,
        )
        return

    class _Handler(watchdog.events.FileSystemEventHandler):
        """Handles file system events and triggers incremental re-indexing."""

        def on_created(self, event: watchdog.events.FileSystemEvent) -> None:
            if not event.is_directory:
                _reindex_file(Path(event.src_path), store, config)

        def on_modified(self, event: watchdog.events.FileSystemEvent) -> None:
            if not event.is_directory:
                _reindex_file(Path(event.src_path), store, config)

        def on_deleted(self, event: watchdog.events.FileSystemEvent) -> None:
            if not event.is_directory:
                _remove_source(Path(event.src_path), store)

        def on_moved(self, event: watchdog.events.FileSystemEvent) -> None:
            if not event.is_directory:
                # Remove old path, index new path
                _remove_source(Path(event.src_path), store)
                _reindex_file(Path(event.dest_path), store, config)

    _observer = watchdog.observers.Observer()
    _observer.schedule(_Handler(), str(doc_dir), recursive=True)
    _observer.daemon = True
    _observer.start()

    logger.info("File watcher started on: %s", doc_dir)


def stop_watcher() -> None:
    """Stop the background file watcher."""
    global _observer
    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=5)
        _observer = None
        logger.info("File watcher stopped.")


# ── Helpers (called from watcher thread) ──────────────────────────────

def _reindex_file(
    file_path: Path,
    store: VectorStoreInterface,
    config: RAGConfig,
) -> None:
    """Re-index a single file (remove old + add new chunks)."""
    try:
        if not file_path.is_file():
            return

        # Remove old entries for this source
        store.delete_by_source(str(file_path))

        # Load, split, index
        from rag_kb.loader import load_file
        from rag_kb.splitter import split_documents

        docs = load_file(str(file_path))
        chunks = split_documents(docs, config)
        from rag_kb.interfaces import IndexedDoc

        indexed = [
            IndexedDoc(content=chunk.page_content, metadata=chunk.metadata)
            for chunk in chunks
        ]
        count = store.add_documents(indexed)
        logger.info(
            "Watcher: re-indexed '%s' (%d chunks)", file_path.name, count
        )
    except Exception as e:
        logger.warning("Watcher: failed to re-index '%s': %s", file_path, e)


def _remove_source(file_path: Path, store: VectorStoreInterface) -> None:
    """Remove all chunks for a deleted source file."""
    try:
        count = store.delete_by_source(str(file_path))
        if count > 0:
            logger.info(
                "Watcher: removed %d chunks for deleted '%s'",
                count, file_path.name,
            )
    except Exception as e:
        logger.warning(
            "Watcher: failed to remove '%s': %s", file_path, e
        )
