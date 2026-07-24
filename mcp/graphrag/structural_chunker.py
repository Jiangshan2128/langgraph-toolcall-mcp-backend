"""Override GraphRAG text splitter — chunk by markdown headings + paragraphs.

Installed by GraphRAG: patch ``text_splitting/text_splitting.py`` in site-packages.
Or set ``PYTHONPATH`` to point here before ``graphrag/index/text_splitting/text_splitting.py``.
"""
import re
from collections.abc import Callable, Iterable
from typing import Any


class TokenTextSplitter:
    """Drop-in replacement — splits by markdown headings, then by paragraphs,
    then by tokens (if still over chunk_size)."""

    def __init__(
        self,
        chunk_size: int = 1200,
        chunk_overlap: int = 100,
        tokenizer: Any | None = None,
        **kwargs: Any,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._encode = getattr(tokenizer, "encode", None) or (lambda t: [0] * len(t))
        self._decode = getattr(tokenizer, "decode", None) or (lambda t: "")

    def split_text(self, text: str) -> list[str]:
        """Split by: heading blocks → paragraph blocks → token cap."""
        return split_text_on_structure(text, self.chunk_size, self.chunk_overlap)


def split_text_on_structure(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Hierarchical split: headings → paragraphs → tokens."""
    heading_blocks = re.split(r"(?=^#{1,3}\s)", text, flags=re.MULTILINE)
    chunks: list[str] = []
    buffer = ""

    for block in heading_blocks:
        block = block.strip()
        if not block:
            continue
        paras = [p.strip() for p in block.split("\n\n") if p.strip()]

        # Estimate token count (simple char-based ratio)
        buf_tokens = len(buffer) // 4
        block_tokens = sum(len(p) // 4 for p in paras)

        # If block fits in current buffer, append
        if buf_tokens + block_tokens <= chunk_size:
            for p in paras:
                buffer += "\n\n" + p if buffer else p
            continue

        # Flush buffer
        if buffer:
            chunks.append(buffer)
            buffer = ""

        # Distribute paragraphs across chunks
        # If a single paragraph is too long, fall back to token split
        for p in paras:
            p_tokens = len(p) // 4
            if p_tokens > chunk_size:
                # Fallback to token-split for this paragraph
                if buffer:
                    chunks.append(buffer)
                    buffer = ""
                for token_chunk in _token_split(p, chunk_size, overlap):
                    chunks.append(token_chunk)
            elif len(buffer) // 4 + p_tokens > chunk_size:
                chunks.append(buffer)
                buffer = p
            else:
                buffer += "\n\n" + p if buffer else p

    if buffer:
        chunks.append(buffer)

    return chunks or [text]


def _token_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Simple char-based token split fallback."""
    words = text.split()
    chunks: list[str] = []
    n = chunk_size
    o = overlap
    i = 0
    while i < len(words):
        chunk = " ".join(words[i: i + n])
        chunks.append(chunk)
        i += n - o
    return chunks or [text]
