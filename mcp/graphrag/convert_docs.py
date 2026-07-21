#!/usr/bin/env python3
"""Convert DOCX files to Markdown for GraphRAG indexing.

Reads DOCX files from ``knowledge_base/documents/``, runs them through the
existing Pandoc pipeline, and writes clean Markdown to ``mcp/graphrag/input/``.

Usage::

    cd backend
    .venv\\Scripts\\python mcp/graphrag/convert_docs.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# ── sys.path setup ──────────────────────────────────────────────────
_mcp_path = Path(__file__).resolve().parents[1]
if str(_mcp_path) not in sys.path:
    sys.path.insert(0, str(_mcp_path))

from rag_kb.loader import load_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("convert_docs")

# Directories
_DOCUMENTS_DIR = _mcp_path / "knowledge_base" / "documents"
_OUTPUT_DIR = Path(__file__).resolve().parent / "input"

# Office temp files and cache to skip
_SKIP_PREFIXES = ("~$", "~", ".")


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    docx_files = sorted(
        f for f in _DOCUMENTS_DIR.glob("*.docx")
        if not f.name.startswith(_SKIP_PREFIXES)
    )

    if not docx_files:
        logger.warning("No DOCX files found in %s", _DOCUMENTS_DIR)
        return

    logger.info("Found %d DOCX file(s) in %s", len(docx_files), _DOCUMENTS_DIR)

    for docx_path in docx_files:
        txt_name = docx_path.stem + ".txt"
        output_path = _OUTPUT_DIR / txt_name

        logger.info("Converting: %s → %s", docx_path.name, txt_name)

        try:
            docs = load_file(str(docx_path))
            content = "\n\n".join(doc.page_content for doc in docs)
            output_path.write_text(content, encoding="utf-8")
            logger.info(
                "  → %s: %d chars, %d lines",
                txt_name, len(content), content.count("\n") + 1,
            )
        except Exception as e:
            logger.error("  → Failed: %s", e)

    # List output
    output_files = sorted(_OUTPUT_DIR.glob("*.md"))
    logger.info(
        "Done. %d Markdown file(s) in %s",
        len(output_files), _OUTPUT_DIR,
    )
    for f in output_files:
        logger.info("  - %s", f.name)


if __name__ == "__main__":
    main()
