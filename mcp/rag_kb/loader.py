"""Document loader — loads files of various formats into LangChain Document objects.

Supported formats: txt, md, pdf, csv, json, html, docx
"""

from __future__ import annotations

import logging
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
    """Load a plain text file."""
    text = file_path.read_text(encoding="utf-8")
    return [Document(page_content=text, metadata={"source": str(file_path)})]


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
    """Load a DOCX file using Docx2txtLoader."""
    try:
        from langchain_community.document_loaders import Docx2txtLoader
    except ImportError:
        raise ImportError(
            "docx2txt is required for DOCX support. "
            "Install: pip install docx2txt"
        )

    loader = Docx2txtLoader(str(file_path))
    return loader.load()
