"""RAG knowledge base components.

Modules
=======
interfaces            Abstract interfaces (VectorStoreInterface, SearchResult, IndexedDoc)
config                Configuration via pydantic-settings
embeddings            Embedding model factory (OpenAI-compatible / HuggingFace)
loader                Document loader (txt, md, pdf, csv, json, html, docx)
splitter              Text splitter (RecursiveCharacterTextSplitter)
indexer               Document indexer (startup + incremental)
watcher               File system watcher (watchdog)
vector_store_factory  Vector store backend factory
qdrant_store          Qdrant local mode backend
retriever             RAG orchestration layer
"""

from __future__ import annotations
