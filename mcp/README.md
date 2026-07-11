# MCP RAG Knowledge Base Service

基于 **FastMCP** + **LangChain RAG** 框架构建的本地 MCP 知识库查询服务。

## 架构

```
mcp/
├── server.py                  # FastMCP 服务入口，暴露 MCP tools
├── rag_kb/                    # RAG 核心 Python 包
│   ├── config.py              # pydantic-settings 配置（环境变量 / .env）
│   ├── embeddings.py          # Embedding 模型工厂（OpenAI兼容 / HuggingFace本地）
│   ├── loader.py              # 文档加载器（txt, md, pdf, csv, json, html, docx）
│   ├── splitter.py            # 文本分割器（RecursiveCharacterTextSplitter）
│   ├── vector_store.py        # Qdrant 向量存储（本地模式，持久化）
│   └── retriever.py           # RAG 检索编排器（加载→分割→嵌入→检索）
└── knowledge_base/
    ├── documents/             # 待索引文档存放目录
    │   └── .gitkeep
    └── qdrant_data/           # Qdrant 本地持久化目录
```

## 快速开始

### 1. 配置 Embedding

在项目根目录 `.env` 中配置（可选，默认复用 GLM API）：

```env
# 使用 GLM/ZhipuAI Embedding（默认，自动复用 GLM_API_KEY）
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=embedding-2
EMBEDDING_BASE_URL=https://open.bigmodel.cn/api/paas/v4/

# 或使用 OpenAI
# EMBEDDING_PROVIDER=openai
# EMBEDDING_MODEL=text-embedding-3-small
# EMBEDDING_API_KEY=sk-...

# 或使用本地 HuggingFace 模型（无需 API key）
# EMBEDDING_PROVIDER=huggingface
# EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

### 2. 添加文档

将文档放入 `knowledge_base/documents/` 目录，支持格式：

| 格式 | 扩展名 |
|------|--------|
| 纯文本 | `.txt`, `.md`, `.py`, `.js`, `.ts`, `.json`, `.yaml`, `.toml`, `.log` |
| PDF | `.pdf` |
| CSV | `.csv` |
| HTML | `.html`, `.htm` |
| Word | `.docx` |

### 3. 启动服务

```bash
# stdio 传输（默认，用于 Claude Code 等本地客户端）
python mcp/server.py

# HTTP 传输（用于远程访问）
fastmcp run mcp/server.py:mcp --transport http --port 9000
```

### 4. 注册到 mcp_servers.json

在项目根目录 `mcp_servers.json` 中添加：

```json
{
  "mcpServers": {
    "rag-knowledge-base": {
      "enabled": true,
      "type": "stdio",
      "command": "python",
      "args": ["mcp/server.py"],
      "description": "Local RAG knowledge base query service"
    }
  }
}
```

## MCP Tools

| Tool | 描述 |
|------|------|
| `search_knowledge(query, top_k=5)` | 语义搜索知识库，返回相关上下文和来源 |
| `add_document(file_path)` | 添加单个文档到知识库 |
| `add_directory(directory)` | 批量添加目录下所有文档 |
| `list_sources()` | 列出所有已索引的知识来源 |
| `get_context(query, top_k=3)` | 获取原始上下文（用于下游 prompt 注入） |

## 使用示例

```python
# 通过 MCP client 调用
from fastmcp import Client

async with Client("http://localhost:9000/mcp") as client:
    # 添加文档
    result = await client.call_tool("add_document", {
        "file_path": "/path/to/document.pdf"
    })

    # 搜索知识库
    result = await client.call_tool("search_knowledge", {
        "query": "What is LangGraph?",
        "top_k": 5
    })

    # 列出来源
    result = await client.call_tool("list_sources", {})
```

## 配置项

所有配置通过 `.env` 或环境变量设置：

| 变量 | 默认值 | 描述 |
|------|--------|------|
| `EMBEDDING_PROVIDER` | `openai` | Embedding 提供商：`openai` 或 `huggingface` |
| `EMBEDDING_MODEL` | `embedding-2` | Embedding 模型名称 |
| `EMBEDDING_API_KEY` | (空，回退到 `GLM_API_KEY`) | API 密钥 |
| `EMBEDDING_BASE_URL` | (空，回退到 `GLM_BASE_URL`) | API 基础 URL |
| `CHUNK_SIZE` | `1000` | 文本分块大小（字符） |
| `CHUNK_OVERLAP` | `200` | 分块重叠大小 |
| `DEFAULT_TOP_K` | `5` | 默认返回结果数 |
| `MAX_CONTEXT_LENGTH` | `8000` | 检索上下文最大字符数 |
| `QDRANT_PATH` | `mcp/knowledge_base/qdrant_data` | Qdrant 本地存储路径 |
| `QDRANT_COLLECTION` | `ai_note_knowledge` | Qdrant 集合名称 |
| `QDRANT_DISTANCE` | `cosine` | 距离度量：`cosine`, `euclid`, `dot` |

## 依赖

- `fastmcp>=3.0.0` — MCP 服务框架
- `langchain>=1.3.2` — LLM 抽象层
- `langchain-openai>=0.3.0` — OpenAI 兼容 Embedding
- `langchain-qdrant>=0.1.0` — Qdrant 向量存储集成
- `qdrant-client>=1.9.0` — Qdrant 本地模式客户端
- `pydantic-settings>=2.0.0` — 配置管理
