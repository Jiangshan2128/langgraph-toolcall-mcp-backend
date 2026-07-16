"""Document loader — loads files of various formats into LangChain Document objects.

Supported formats: txt, md, pdf, csv, json, html, docx

For DOCX files, Pandoc is used (when available) to convert to GFM Markdown,
with automatic TOC stripping and HTML-table-to-pipe-table cleaning.
Falls back to docx2txt if Pandoc is not installed.
"""

from __future__ import annotations

import html as _html_mod
import logging
import re
import shutil
import subprocess
from pathlib import Path

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

# Map file extensions to loader factories
_LOADER_MAP: dict[str, str] = {
    ".txt": "text",
    ".md": "text",
    ".py": "text",
    ".js": "text",
    ".ts": "text",
    ".json": "text",
    ".yaml": "text",
    ".yml": "text",
    ".toml": "text",
    ".cfg": "text",
    ".ini": "text",
    ".log": "text",
    ".csv": "csv",
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
    ".docx": "docx",
}


def load_file(file_path: str | Path) -> list[Document]:
    """Load a single file into a list of Document objects.

    Args:
        file_path: Path to the file to load.

    Returns:
        List of Document objects with ``source`` metadata.

    Raises:
        ValueError: If the file format is unsupported.
        FileNotFoundError: If the file does not exist.
    """
    file_path = Path(file_path).resolve()

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = file_path.suffix.lower()
    loader_type = _LOADER_MAP.get(suffix)

    if loader_type is None:
        raise ValueError(
            f"Unsupported file format: {suffix!r}. "
            f"Supported: {', '.join(sorted(set(_LOADER_MAP.values())))}"
        )

    logger.info("Loading document: %s (type=%s)", file_path.name, loader_type)
    docs = _load_by_type(file_path, loader_type)

    # Ensure source metadata
    for doc in docs:
        if "source" not in doc.metadata:
            doc.metadata["source"] = str(file_path)
        doc.metadata["file_name"] = file_path.name

    logger.info("Loaded %d document(s) from %s", len(docs), file_path.name)
    return docs


def load_directory(directory: str | Path) -> list[Document]:
    """Load all supported files from a directory (non-recursive).

    Args:
        directory: Path to the directory.

    Returns:
        Combined list of Document objects from all supported files.
    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    all_docs: list[Document] = []
    supported_exts = tuple(_LOADER_MAP.keys())

    for file_path in sorted(directory.iterdir()):
        if file_path.is_file() and file_path.suffix.lower() in supported_exts:
            try:
                docs = load_file(file_path)
                all_docs.extend(docs)
            except Exception as e:
                logger.warning("Skipping %s: %s", file_path.name, e)

    logger.info(
        "Loaded %d document(s) from directory %s", len(all_docs), directory.name
    )
    return all_docs


def _load_by_type(file_path: Path, loader_type: str) -> list[Document]:
    """Route to the appropriate loader based on type."""
    if loader_type == "text":
        return _load_text(file_path)
    elif loader_type == "csv":
        return _load_csv(file_path)
    elif loader_type == "pdf":
        return _load_pdf(file_path)
    elif loader_type == "html":
        return _load_html(file_path)
    elif loader_type == "docx":
        return _load_docx(file_path)
    else:
        raise ValueError(f"Unknown loader type: {loader_type!r}")


def _load_text(file_path: Path) -> list[Document]:
    """Load a plain text (or Markdown) file."""
    text = file_path.read_text(encoding="utf-8")
    suffix = file_path.suffix.lower()
    fmt = "markdown" if suffix == ".md" else "text"
    return [Document(page_content=text, metadata={"source": str(file_path), "format": fmt})]


def _load_csv(file_path: Path) -> list[Document]:
    """Load a CSV file using LangChain's CSVLoader."""
    from langchain_community.document_loaders import CSVLoader

    loader = CSVLoader(str(file_path), encoding="utf-8")
    return loader.load()


def _load_pdf(file_path: Path) -> list[Document]:
    """Load a PDF file using PyPDFLoader."""
    try:
        from langchain_community.document_loaders import PyPDFLoader
    except ImportError:
        raise ImportError(
            "pypdf is required for PDF support. Install: pip install pypdf"
        )

    loader = PyPDFLoader(str(file_path))
    return loader.load()


def _load_html(file_path: Path) -> list[Document]:
    """Load an HTML file using BSHTMLLoader."""
    try:
        from langchain_community.document_loaders import BSHTMLLoader
    except ImportError:
        raise ImportError(
            "beautifulsoup4 is required for HTML support. "
            "Install: pip install beautifulsoup4 lxml"
        )

    loader = BSHTMLLoader(str(file_path))
    return loader.load()


def _load_docx(file_path: Path) -> list[Document]:
    """Load a DOCX file: Pandoc → GFM Markdown (primary) or docx2txt (fallback).

    Pandoc path:
        1. ``pandoc -t gfm`` → Markdown (headings, lists, footnotes)
        2. HTML tables → GFM pipe tables
        3. Strip TOC section

    Falls back to Docx2txtLoader if Pandoc is not installed.
    """
    if shutil.which("pandoc"):
        return _load_docx_via_pandoc(file_path)

    logger.warning(
        "Pandoc not found — falling back to docx2txt (structure will be lost). "
        "Install Pandoc: winget install JohnMacFarlane.Pandoc"
    )
    try:
        from langchain_community.document_loaders import Docx2txtLoader
    except ImportError:
        raise ImportError(
            "docx2txt is required for DOCX support when Pandoc is unavailable. "
            "Install: pip install docx2txt"
        )
    loader = Docx2txtLoader(str(file_path))
    docs = loader.load()
    for doc in docs:
        doc.metadata["format"] = "text"
    return docs


# ---------------------------------------------------------------------------
#  DOCX → Pandoc → Markdown helpers
# ---------------------------------------------------------------------------

def _clean_markdown_noise(markdown: str) -> str:
    """Strip DOCX-to-Markdown conversion artifacts.

    - ``<span class="mark">`` / ``</span>`` — highlighted-text wrappers
      (keep inner content, discard the tag).
    - ``<img ...>`` — image references; the knowledge base does not store
      the actual image files so these are useless for retrieval.
    - ``<!-- -->`` — empty HTML comments Pandoc emits for page/section
      breaks in Word.
    - ``<!-- ... -->`` — non-empty HTML comments (rare, but noise).
    """
    # Strip <span> / </span> wrappers, preserve content
    markdown = re.sub(r'<span[^>]*>', '', markdown)
    markdown = re.sub(r'</span>', '', markdown)
    # Strip <img> tags (self-closing or not)
    markdown = re.sub(r'<img[^>]*/?>', '', markdown)
    # Strip empty & non-empty HTML comments
    markdown = re.sub(r'<!--.*?-->', '', markdown, flags=re.DOTALL)
    # Collapse 3+ consecutive blank lines into 2
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)
    return markdown


def _normalize_list_hierarchy(markdown: str) -> str:
    """Convert indented numbered sub-items to bullet points, and make
    top-level numbering sequential.

    Pandoc preserves DOCX list nesting via indentation (4 spaces per level),
    but every level uses ``1)``, ``2)`` numbering because the DOCX defines
    independent list templates for each nesting level.  This causes two
    problems:

    1. Indented sub-items are indistinguishable from new top-level lists.
    2. A new list template resets the counter to 1 mid-section::

           1)  数量：1块
           2)  总线接口：PXIe
           3)  测试接口：...
           1)  工作模式：          ← Word list-template switch; "1)" again
           2)  支持2路DVI/DP采集

    Fix:

    - Indented ``N)`` → ``-`` (bullet).
    - Top-level items are renumbered sequentially.  The counter resets
      only on blank lines or ATX headings, so items that belong to the
      same section always flow ``1) 2) 3) 4) …``.
    """
    lines = markdown.split('\n')

    # ---- pass 1: indented items → bullets --------------------------------
    result: list[str] = []
    for line in lines:
        m = re.match(r'^(\s+)(\d+)\)\s+', line)
        if m:
            indent = m.group(1)
            result.append(f'{indent}- ')
            result[-1] += line[m.end():]
        else:
            result.append(line)

    # ---- pass 2: sequential top-level numbering --------------------------
    _TOP_ITEM = re.compile(r'^(\d+)\)(\s+)')
    _HEADING = re.compile(r'^#{1,4}\s+')
    counter = 0  # 0 = inactive (haven't seen a numbered item yet in this block)

    for i, line in enumerate(result):
        m = _TOP_ITEM.match(line)
        if m:
            if counter == 0:
                counter = 1
            result[i] = f'{counter}){m.group(2)}{line[m.end():]}'
            counter += 1
        elif _HEADING.match(line):
            counter = 0                          # new section → restart

    return '\n'.join(result)

    return '\n'.join(result)

def _load_docx_via_pandoc(file_path: Path) -> list[Document]:
    """Convert a DOCX to clean Markdown via Pandoc subprocess."""
    result = subprocess.run(
        ["pandoc", str(file_path), "-t", "gfm", "--wrap=preserve"],
        capture_output=True, text=True, check=True,
    )
    markdown = result.stdout

    # Post-processing pipeline
    markdown = _html_table_to_pipe_table(markdown)
    markdown = _strip_markdown_toc(markdown)
    markdown = _clean_markdown_noise(markdown)
    markdown = _normalize_list_hierarchy(markdown)

    logger.info(
        "DOCX → Markdown via Pandoc: %d chars, %d lines",
        len(markdown), markdown.count('\n') + 1,
    )

    return [Document(
        page_content=markdown,
        metadata={"source": str(file_path), "format": "markdown"},
    )]


def _strip_markdown_toc(markdown: str) -> str:
    """Remove the table-of-contents section from Markdown.

    Works in two passes:

    1. **Heading-based**: if an explicit ``# 目录`` / ``# Table of Contents``
       heading exists, delete it and everything until the next heading of
       equal or higher level.

    2. **Pattern-based**: detect TOC entry clusters — lines that look like
       ``[text [N]](#anchor)`` (Markdown links with embedded page numbers).
       When ≥ 5 consecutive TOC-pattern lines appear, the entire cluster is
       stripped.  Sub-entries prefixed with ``>`` (blockquote) are included.
    """
    lines = markdown.split('\n')
    result: list[str] = []

    # ----------------------------------------------------------------
    #  Pass 1: heading-based ("# 目录" / "# Table of Contents")
    # ----------------------------------------------------------------
    in_heading_toc = False
    toc_heading_level = 0

    for line in lines:
        m = re.match(
            r'^(#{1,4})\s*(目\s*录|Table\s+of\s+Contents|TOC)\s*$',
            line, re.IGNORECASE,
        )
        if m:
            in_heading_toc = True
            toc_heading_level = len(m.group(1))
            continue

        if in_heading_toc:
            heading = re.match(r'^(#{1,4})\s+', line)
            if heading and len(heading.group(1)) <= toc_heading_level:
                in_heading_toc = False

        if not in_heading_toc:
            result.append(line)

    # ----------------------------------------------------------------
    #  Pass 2: pattern-based (link cluster with page numbers)
    # ----------------------------------------------------------------
    # TOC entry pattern:
    #   "[title text [page_number]](#anchor)"       → top-level
    #   "> [title text [page_number]](#anchor)"     → indented sub-entry
    _TOC_RE = re.compile(
        r'^(>\s*)?\[.+?\s+\[\d+\]\(#[^)]*\)\]\(#[^)]*\)\s*$'
    )

    result2: list[str] = []
    # Collect indices of lines matching TOC pattern
    toc_line_set: set[int] = set()
    for i, line in enumerate(result):
        if _TOC_RE.match(line):
            toc_line_set.add(i)

    # Find contiguous clusters of ≥ 5 TOC lines (allowing blanks)
    i = 0
    while i < len(result):
        if i in toc_line_set:
            # Start of a potential cluster — scan forward
            cluster_end = i
            toc_count = 1
            j = i + 1
            while j < len(result):
                if j in toc_line_set:
                    toc_count += 1
                    cluster_end = j
                    j += 1
                elif result[j].strip() in ('', '>'):
                    # Blank lines and standalone blockquote separators
                    # (Pandoc uses ">" on its own line between TOC entries)
                    # are OK within a dense cluster.
                    j += 1
                else:
                    break
            # If cluster has ≥ 5 TOC lines, skip it
            if toc_count >= 5:
                logger.debug(
                    "Stripped pattern-based TOC: lines %d-%d (%d TOC entries)",
                    i + 1, cluster_end + 1, toc_count,
                )
                i = cluster_end + 1
                # Also skip trailing blank lines after the cluster
                while i < len(result) and result[i].strip() == '':
                    i += 1
                continue

        result2.append(result[i])
        i += 1

    return '\n'.join(result2)


# ---------------------------------------------------------------------------
#  HTML table → GFM pipe table converter
# ---------------------------------------------------------------------------

def _html_table_to_pipe_table(html_text: str) -> str:
    """Convert residual HTML ``<table>`` tags to GFM pipe tables.

    Strips ``<colgroup>``, ``<caption>``, and style attributes.
    Handles ``colspan`` expansion; ``rowspan`` is simplified away.
    """
    html_text = _expand_cell_spans(html_text)
    html_text = re.sub(r'<colgroup[^>]*>.*?</colgroup>', '', html_text, flags=re.DOTALL)
    html_text = re.sub(r'<col[^>]*/?>', '', html_text)
    html_text = re.sub(r'<caption[^>]*>.*?</caption>', '', html_text, flags=re.DOTALL)

    def _replace_table(m: re.Match) -> str:
        return _convert_one_table(m.group(0))

    return re.sub(
        r'<table[^>]*>(.*?)</table>',
        _replace_table, html_text,
        flags=re.DOTALL,
    )


def _expand_cell_spans(html_text: str) -> str:
    """Expand colspan / rowspan so every row has the same number of cells."""
    def _expand_colspan(m: re.Match) -> str:
        tag = m.group(0)
        n = int(m.group(1))
        if n <= 1:
            return tag
        clean = re.sub(
            r'\s*colspan\s*=\s*["\']?\d+["\']?', '', tag,
            flags=re.IGNORECASE,
        )
        return clean + "<td></td>" * (n - 1)

    expanded = re.sub(
        r'<(td|th)([^>]*)\s+colspan\s*=\s*["\']?(\d+)["\']?',
        _expand_colspan, html_text,
        flags=re.IGNORECASE,
    )
    # rowspan: simplify by stripping the attribute (content stays in first row)
    expanded = re.sub(
        r'\s*rowspan\s*=\s*["\']?\d+["\']?', '', expanded,
        flags=re.IGNORECASE,
    )
    return expanded


def _convert_one_table(table_html: str) -> str:
    """Convert a single ``<table>...</table>`` block to a pipe table."""
    tr_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
    cell_pattern = re.compile(
        r'<(th|td)([^>]*)>(.*?)</\1>', re.DOTALL | re.IGNORECASE,
    )

    rows: list[list[str]] = []
    for tr in tr_pattern.finditer(table_html):
        cells: list[str] = []
        for cell in cell_pattern.finditer(tr.group(1)):
            cells.append(_clean_cell_content(cell.group(3)))
        if cells:
            rows.append(cells)

    if not rows:
        return ''

    # Normalise column count
    max_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < max_cols:
            r.append('')

    lines: list[str] = []
    # header
    lines.append('| ' + ' | '.join(rows[0]) + ' |')
    # separator
    lines.append('|' + '|'.join(' --- ' for _ in range(max_cols)) + '|')
    # body
    for row in rows[1:]:
        lines.append('| ' + ' | '.join(row) + ' |')

    return '\n' + '\n'.join(lines) + '\n'


def _clean_cell_content(raw: str) -> str:
    """Strip tags, decode entities, collapse whitespace, escape pipes."""
    text = re.sub(r'<br\s*/?>', ' ', raw, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = _html_mod.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.replace('|', r'\|')
    return text
