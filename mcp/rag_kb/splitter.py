"""Document splitter — splits documents into overlapping chunks for indexing."""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_kb.config import RAGConfig


def create_splitter(config: RAGConfig) -> RecursiveCharacterTextSplitter:
    """Create a text splitter based on configuration.

    Uses ``RecursiveCharacterTextSplitter`` with configurable chunk size and
    overlap.  This is the recommended splitter for most RAG use cases —
    it tries to split on natural boundaries (paragraphs, sentences) before
    falling back to character-level splits.
    """
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
    """Split documents into overlapping chunks.

    Args:
        docs: Source documents to split.
        config: RAG configuration for chunk size/overlap.

    Returns:
        Chunked documents with ``start_index`` in metadata.
    """
    splitter = create_splitter(config)
    chunks = splitter.split_documents(docs)
    return chunks
