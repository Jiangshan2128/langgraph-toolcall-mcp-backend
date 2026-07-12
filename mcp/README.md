# MCP RAG Knowledge Base Service

基于 **FastMCP** + **LangChain RAG** 框架构建的本地 MCP 知识库查询服务。

## 架构

```
mcp/
├── server.py                      # 瘦入口：import → 注册 @mcp.tool → mcp.run()
├── rag_kb/                        # RAG 核心 Python 包
│   ├── tools/                     # MCP 工具模块（按领域拆分）
│   │   ├── __init__.py            # mcp 实例、retriever 单例
│   │   ├── search.py              # search_docs, get_document, list_docs
│   │   └── index.py               # refresh_index, get_doc_stats
│   ├── interfaces.py              # 抽象接口（VectorStoreInterface, SearchResult, IndexedDoc）
│   ├── config.py                  # pydantic-settings 配置（环境变量 / .env）
│   ├── embeddings.py              # Embedding 模型工厂（OpenAI兼容 / HuggingFace本地）
│   ├── loader.py                  # 文档加载器（txt, md, pdf, csv, json, html, docx）
│   ├── splitter.py                # 文本分割器（RecursiveCharacterTextSplitter）
│   ├── indexer.py                 # 文档索引器（启动全量索引 + 增量更新）
│   ├── watcher.py                 # 文件监听器（watchdog，运行时自动更新）
│   ├── vector_store_factory.py    # 向量存储工厂（按配置选择后端）
│   ├── qdrant_store.py            # Qdrant 本地模式后端实现
│   └── retriever.py               # RAG 检索编排器（后端无关）
└── knowledge_base/
    ├── documents/                 # 文档存放目录
    └── qdrant_data/               # Qdrant 本地持久化目录
```

### 设计要点

| 原则 | 实现 |
|------|------|
| **低耦合** | `VectorStoreInterface` 抽象接口，Qdrant 和未来 Supabase 实现可互换 |
| **启动自动索引** | 服务启动时扫描 `documents/` 目录，自动全量索引 |
| **增量更新** | 基于 MD5 内容哈希，只处理变更文件 |
| **文件监听** | 可选 watchdog 实时监听，文件变更自动更新 |
| **路径安全** | `get_document` 防止路径遍历攻击 |

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

> **文档会自动在服务启动时索引**。添加新文件后，调用 `refresh_index` 或重启服务即可。

### 3. 启动服务

```bash
# stdio 传输（默认，用于 Claude Code 等本地客户端）
python mcp/server.py

# SSE 传输（用于 Inspector 调试）
uv run fastmcp run mcp/server.py:mcp --transport sse --port 9000
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

## MCP Inspector 调试

[MCP Inspector](https://github.com/modelcontextprotocol/inspector) 是官方的 MCP 调试 UI，可以查看工具列表、调用测试、查看响应。

### 快速启动（推荐）

```bash
# 1. 先启动 MCP Server（SSE 模式）
uv run fastmcp run mcp/server.py:mcp --transport sse --port 9000

# 2. 另一个终端启动 Inspector
npx @modelcontextprotocol/inspector --transport sse --server-url http://127.0.0.1:9000/sse
```

启动后浏览器自动打开 `http://localhost:6274`，即可看到工具列表。

### 端口被占用时

```bash
# 杀掉占用 6274 / 6277 / 9000 端口的进程
Get-Process -Id (Get-NetTCPConnection -LocalPort 6274 -ErrorAction SilentlyContinue).OwningProcess | Stop-Process -Force
Get-Process -Id (Get-NetTCPConnection -LocalPort 6277 -ErrorAction SilentlyContinue).OwningProcess | Stop-Process -Force
Get-Process -Id (Get-NetTCPConnection -LocalPort 9000 -ErrorAction SilentlyContinue).OwningProcess | Stop-Process -Force
```

### 浏览器打不开时

如果自动打开浏览器失败，手动访问：

```
http://localhost:6274/?MCP_PROXY_AUTH_TOKEN=<日志中显示的 token>
```

### 各组件地址

| 组件 | 地址 |
|------|------|
| 🖥️ Inspector UI | `http://localhost:6274` |
| 🔗 Proxy Server | `localhost:6277` |
| ⚡ MCP Server (SSE) | `http://127.0.0.1:9000/sse` |

### 停止服务

按 `Ctrl+C` 停止 server 和 inspector。

## MCP Tools

### 搜索与检索

| Tool | 参数 | 描述 |
|------|------|------|
| `search_docs(query, top_k=5)` | `query: str`, `top_k: int` | 语义搜索知识库，返回相关上下文和来源 |
| `get_document(path)` | `path: str` | 按相对路径读取完整文档（如 `api/auth.md`） |
| `list_docs()` | 无 | 列出所有已索引的知识来源 |

### 索引管理

| Tool | 参数 | 描述 |
|------|------|------|
| `refresh_index(full_rebuild=False)` | `full_rebuild: bool` | 手动触发文档重新索引 |
| `get_doc_stats()` | 无 | 查看知识库统计信息 |

## 配置项

所有配置通过 `.env` 或环境变量设置：

| 变量 | 默认值 | 描述 |
|------|--------|------|
| **向量存储** | | |
| `VECTOR_STORE_BACKEND` | `qdrant` | 后端：`qdrant` 或 `supabase`（未来） |
| `QDRANT_PATH` | `mcp/knowledge_base/qdrant_data` | Qdrant 本地存储路径 |
| `QDRANT_COLLECTION` | `ai_note_knowledge` | Qdrant 集合名称 |
| `QDRANT_DISTANCE` | `cosine` | 距离度量：`cosine`, `euclid`, `dot` |
| **Embedding** | | |
| `EMBEDDING_PROVIDER` | `openai` | 提供商：`openai` 或 `huggingface` |
| `EMBEDDING_MODEL` | `embedding-2` | 模型名称 |
| `EMBEDDING_API_KEY` | (回退到 `GLM_API_KEY`) | API 密钥 |
| `EMBEDDING_BASE_URL` | (回退到 `GLM_BASE_URL`) | API 基础 URL |
| **索引** | | |
| `AUTO_INDEX_ON_START` | `true` | 启动时自动全量索引 |
| `WATCH_ENABLED` | `false` | 启用文件系统监听（需安装 watchdog） |
| **分块** | | |
| `CHUNK_SIZE` | `1000` | 文本分块大小（字符） |
| `CHUNK_OVERLAP` | `200` | 分块重叠大小 |
| **检索** | | |
| `DEFAULT_TOP_K` | `5` | 默认返回结果数 |
| `MAX_CONTEXT_LENGTH` | `8000` | 检索上下文最大字符数 |

## 切换到 Supabase

当需要切换到 Supabase/pgvector 时：

1. 创建 `rag_kb/supabase_store.py`，实现 `VectorStoreInterface`
2. 在 `vector_store_factory.py` 中注册 `"supabase"` 分支
3. 在 `.env` 中设置 `VECTOR_STORE_BACKEND=supabase` 并配置 `SUPABASE_*` 环境变量

```python
# rag_kb/supabase_store.py 骨架
from rag_kb.interfaces import VectorStoreInterface, SearchResult, IndexedDoc

class SupabaseStore(VectorStoreInterface):
    def add_documents(self, chunks: list[IndexedDoc]) -> int: ...
    def delete_by_source(self, source: str) -> int: ...
    def similarity_search(self, query: str, k: int = 5) -> list[SearchResult]: ...
    def get_document_count(self) -> int: ...
    def list_sources(self) -> dict[str, int]: ...
    def close(self) -> None: ...
```

所有上层代码（`retriever.py`、`server.py`）**无需任何修改**。

## 依赖

- `fastmcp>=3.0.0` — MCP 服务框架
- `langchain>=1.3.2` — LLM 抽象层
- `langchain-openai>=0.3.0` — OpenAI 兼容 Embedding
- `langchain-qdrant>=0.1.0` — Qdrant 向量存储集成
- `qdrant-client>=1.9.0` — Qdrant 本地模式客户端
- `pydantic-settings>=2.0.0` — 配置管理
- `watchdog`（可选）— 文件系统监听
