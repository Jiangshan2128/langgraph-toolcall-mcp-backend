"""Document splitter — splits documents into overlapping chunks for indexing.

For **Markdown** documents (from DOCX via Pandoc, or ``.md`` files):
uses ``MarkdownHeaderTextSplitter`` for deterministic section splitting
followed by ``RecursiveCharacterTextSplitter`` for oversized sections.

For **plain text** documents: falls back to ``RecursiveCharacterTextSplitter``
directly.
"""

from __future__ import annotations

import logging
import re

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_kb.config import RAGConfig

logger = logging.getLogger(__name__)

# Headers recognised as section boundaries when splitting Markdown.
_MARKDOWN_HEADERS = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
    ("####", "h4"),
]


def create_splitter(config: RAGConfig) -> RecursiveCharacterTextSplitter:
    """Create a recursive character splitter."""
    return RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        add_start_index=True,
        separators=[
            "\n\n",     # paragraphs
            "\n",       # lines
            ". ",       # sentences (English)
            "。",       # sentences (Chinese)
            "！",       # exclamation (Chinese)
            "？",       # question (Chinese)
            " ",        # words
            "",         # characters
        ],
    )


def split_documents(
    docs: list[Document],
    config: RAGConfig,
) -> list[Document]:
    """Split documents into chunks.

    Markdown documents → ``MarkdownHeaderTextSplitter`` (by ``#`` levels).
    Plain text → ``RecursiveCharacterTextSplitter`` directly.
    """
    markdown_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_MARKDOWN_HEADERS,
        strip_headers=False,
    )
    recursive_splitter = create_splitter(config)

    all_chunks: list[Document] = []

    for doc in docs:
        fmt = doc.metadata.get("format", "text")

        if fmt == "markdown":
            chunks = _split_markdown_doc(
                doc, markdown_splitter, recursive_splitter, config,
            )
        else:
            chunks = recursive_splitter.split_documents([doc])
            for c in chunks:
                c.metadata["format"] = "text"

        all_chunks.extend(chunks)

    logger.info(
        "Split %d doc(s) → %d chunk(s)", len(docs), len(all_chunks),
    )
    return all_chunks


# ---------------------------------------------------------------------------
#  Markdown header-based splitting
# ---------------------------------------------------------------------------

def _split_markdown_doc(
    doc: Document,
    header_splitter: MarkdownHeaderTextSplitter,
    rec_splitter: RecursiveCharacterTextSplitter,
    config: RAGConfig,
) -> list[Document]:
    """Split a Markdown document: header sections first, numbered-item-aware
    splitting for list-heavy sections, recursive for the rest."""
    source = doc.metadata.get("source", "")
    file_name = doc.metadata.get("file_name", "")

    try:
        header_chunks = header_splitter.split_text(doc.page_content)
    except Exception:
        logger.exception("MarkdownHeaderTextSplitter failed, falling back to recursive")
        header_chunks = [Document(page_content=doc.page_content)]

    final_chunks: list[Document] = []

    for chunk in header_chunks:
        if len(chunk.page_content) <= config.CHUNK_SIZE:
            final_chunks.append(chunk)
        else:
            heading_prefix = _build_heading_prefix(chunk.metadata)
            # For list-heavy sections (≥3 top-level numbered items),
            # prefer splitting on item boundaries so each item stays
            # intact with its sub-items.
            if _count_top_level_items(chunk.page_content) >= 2:
                item_chunks = _split_by_numbered_items(
                    chunk, heading_prefix, config, rec_splitter,
                )
                final_chunks.extend(item_chunks)
            else:
                sub_chunks = rec_splitter.split_documents([chunk])
                for sub in sub_chunks:
                    if heading_prefix:
                        sub.page_content = heading_prefix + "\n\n" + sub.page_content
                    for key in ("h1", "h2", "h3", "h4"):
                        if key in chunk.metadata:
                            sub.metadata[key] = chunk.metadata[key]
                    sub.metadata["format"] = "markdown"
                final_chunks.extend(sub_chunks)

    for i, chunk in enumerate(final_chunks):
        if "source" not in chunk.metadata:
            chunk.metadata["source"] = source
        if "file_name" not in chunk.metadata:
            chunk.metadata["file_name"] = file_name
        chunk.metadata["chunk_index"] = i

    return final_chunks


def _build_heading_prefix(metadata: dict) -> str:
    """Build a heading breadcrumb from h1→h4 metadata, e.g.
    ``## 云台子系统\n### 电机选型``.
    """
    parts: list[str] = []
    for key in ("h1", "h2", "h3", "h4"):
        val = metadata.get(key)
        if val:
            level = int(key[1])
            parts.append("#" * level + " " + val)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
#  Numbered-item-aware splitting
# ---------------------------------------------------------------------------

# Matches a top-level numbered item: line-start, digits, ")", whitespace, content.
# Sub-items (indented) are *not* matched — they stay attached to their parent.
_TOP_LEVEL_ITEM_RE = re.compile(r'\n(?=\d+\)\s+\S)')


def _count_top_level_items(text: str) -> int:
    """Count top-level numbered items (e.g. ``1) ...``, ``12) ...``)."""
    return len(_TOP_LEVEL_ITEM_RE.findall(text))


def _split_by_numbered_items(
    chunk: Document,
    heading_prefix: str,
    config: RAGConfig,
    rec_splitter: RecursiveCharacterTextSplitter,
) -> list[Document]:
    """Split an oversized, list-heavy section on top-level numbered-item
    boundaries.

    Strategy:

    1. Split on ``\\n1)``, ``\\n2)``, … boundaries (top-level items only;
       indented sub-items stay attached to their parent).
    2. Group consecutive items that together fit within *chunk_size*.
    3. Every chunk is prefixed with the full heading breadcrumb
       (``# h1\\n## h2``), so a retrieved chunk always carries its
       document→section ancestry.
    4. A single item that still exceeds *chunk_size* falls through to
       ``RecursiveCharacterTextSplitter`` (with breadcrumb).
    """
    content = chunk.page_content
    # Strip heading lines that MarkdownHeaderTextSplitter left in the
    # content — the breadcrumb already supplies them.
    lines = content.split('\n')
    while lines and re.match(r'^#{1,4}\s+', lines[0]):
        lines.pop(0)
    # Also consume a single leading blank line after the heading(s)
    if lines and lines[0].strip() == '':
        lines.pop(0)
    content = '\n'.join(lines)
    parts = _TOP_LEVEL_ITEM_RE.split(content)

    # First segment = any text before the first numbered item (intro / preamble).
    intro = parts[0].strip() if parts else ""
    items = parts[1:] if len(parts) > 1 else []

    prefix_overhead = len(heading_prefix) + 2  # +2 for the \n\n separator

    result: list[Document] = []
    current_group: list[str] = []
    current_len = 0

    # If there's substantive intro text before the first item, start with it.
    if intro:
        current_group.append(intro)
        current_len = len(intro)

    for item in items:
        item_text = item.strip()
        if not item_text:
            continue
        item_len = len(item_text)

        # -- Single item exceeds chunk_size → recursive split ----------
        if item_len > config.CHUNK_SIZE - prefix_overhead:
            # Flush accumulated group first
            if current_group:
                result.append(_make_item_chunk(heading_prefix, current_group, chunk.metadata))
                current_group = []
                current_len = 0
            # Recursive split for this oversized item
            item_doc = Document(page_content=item_text)
            subs = rec_splitter.split_documents([item_doc])
            for sub in subs:
                sub.page_content = heading_prefix + "\n\n" + sub.page_content
                for key in ("h1", "h2", "h3", "h4"):
                    if key in chunk.metadata:
                        sub.metadata[key] = chunk.metadata[key]
                sub.metadata["format"] = "markdown"
            result.extend(subs)
            continue

        # -- Adding this item would overflow → flush group ------------
        if current_group and current_len + item_len + 1 > config.CHUNK_SIZE - prefix_overhead:
            result.append(_make_item_chunk(heading_prefix, current_group, chunk.metadata))
            current_group = []
            current_len = 0

        current_group.append(item_text)
        current_len += item_len + 1  # +1 for the joining newline

    # Flush trailing group
    if current_group:
        result.append(_make_item_chunk(heading_prefix, current_group, chunk.metadata))

    return result


def _make_item_chunk(
    heading_prefix: str,
    items: list[str],
    metadata: dict,
) -> Document:
    """Build a Document chunk from a group of numbered items.

    The chunk content is ``heading_prefix + blank-line + items``, so every
    chunk is self-contained with its full document→section ancestry.
    """
    content = heading_prefix + "\n\n" + "\n".join(items)
    doc = Document(page_content=content)
    for key in ("h1", "h2", "h3", "h4"):
        if key in metadata:
            doc.metadata[key] = metadata[key]
    doc.metadata["format"] = "markdown"
    return doc
