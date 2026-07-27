# AI Note Backend

AI-powered todo list 应用的后端服务，也是一个用于学习 AI Agent 架构的实操项目。

基于 **FastAPI + LangGraph + LLM + 延迟工具加载** 构建，支持语音录入、智能任务管理、人工审批（HITL），以及钉钉任务同步。

---

## 功能

| 功能 | 描述 |
|---|---|
| 💬 **对话式任务管理** | 自然语言输入，LLM 自动提取/更新/删除任务 |
| 🎤 **语音转录** | 上传音频 → Groq Whisper → 文本 → 自动提取任务 |
| ✋ **人工审批（HITL）** | 任务变更提案需人工确认/编辑/拒绝后才生效 |
| 📋 **任务 CRUD** | REST API 直接操作任务（查询、修改、删除） |
| 🔗 **钉钉同步** | 审批通过后可一键同步任务到钉钉待办列表 |
| 🧠 **长期记忆** | 用户画像、任务历史、偏好指令持久化到 PostgreSQL |
| 🔧 **MCP 工具生态** | 通过 MCP 协议动态加载外部工具（钉钉、RAG 等） |

---

## 技术亮点

### 架构分层

```
app/          FastAPI Web 层（路由、服务、依赖注入）
packages/     Agent 核心层（graph、tools、middleware、config）
```

Web 层与 Agent 层解耦，Agent 层可独立演进和测试。

### LangGraph 编排引擎

- 不是简单的 LLM + tools 循环，而是**显式拓扑控制**的图结构
- 条件路由：`START → transcription? → agent → tools → HITL? → agent → END`
- 转录子图有独立私有状态和 checkpoint namespace（此处使用subgraph仅为练习用）
- 支持 `interrupt()` 挂起 → 人工审批 → `Command(resume=True)` 恢复

### Middleware Pipeline（俄罗斯套娃模式）

```
ErrorHandlingMiddleware     ← 最外层：捕获所有异常
  └── MemoryLoadMiddleware  ← 从 store 加载 profile/tasks/instructions
      └── SystemPromptMiddleware  ← 用记忆构建系统提示
          └── ToolBindingMiddleware  ← 绑定工具到 ChatOpenAI
              └── LLM 调用
```

每个 middleware 只处理一个关注点，通过 pipeline 组合，可独立测试和替换。

### Tool Binding 分层策略

- **Core tools**（8 个）：始终绑定，包括任务 CRUD 和网页搜索
- **MCP 动态工具**：启动时加载，按需通过 `tool_search` 推广
- 解决 GLM-5.1 有 ~55 个工具上限、但钉钉 MCP 提供 ~98 个工具的问题

### Human-in-the-Loop（HITL）

- TrustCall 确定性提取任务变更 → 生成 proposals（`json_doc_id` 防重放）
- `interrupt()` 挂起，前端展示 diff → 用户 approve/edit/reject
- Resume 后从 checkpoint 恢复，不重新调用 LLM，保证一致性
- 可选钉钉同步：审批通过后自动将变更推送到钉钉待办

### 持久化与容错

- PostgreSQL（Supabase）存业务数据（profile/tasks/instructions）
- LangGraph checkpointing 存对话状态
- 数据库连接失败自动回退到内存存储（开发友好）
- 健康检查启动时验证 store 读写

---

## 可以学到的点

### Agent 编排

- **LangGraph 图结构设计**：如何用 `StateGraph` + 条件边构建复杂的 agent 控制流
- **子图隔离**：私有 state、独立 checkpoint、namespace 管理
- **interrupt / resume 模式**：HITL 的完整实现，包括 resume 后状态恢复和确定性 key 策略
- **Middleware vs Graph Nodes**：何时用 middleware（横切关注点），何时用 graph nodes（拓扑控制）

### 工具系统

- **MCP 协议集成**：如何加载外部工具、动态注册、按需推广
- **工具选择策略**：核心工具 vs 延迟加载，解决 LLM 工具数量上限
- **TrustCall 确定性提取**：通过 `json_doc_id` 防止 HITL resume 时的 key 漂移

### 持久化与配置

- **双层存储**：业务数据（PostgresStore）+ 对话状态（checkpointer）分离
- **优雅降级**：`DATABASE_URL` 未配置时自动回退内存存储
- **App 启动时重建 graph**：MCP 工具加载后重新编译 graph（hot reload）

### 语音与 RAG

- **Groq Whisper 集成**：音频 → 文本 → 结构化任务提取的端到端链路
- **混合检索**：Qdrant dense+sparse 混合搜索用于知识库问答

### 工程实践

- **uv workspace monorepo**：`app/` 和 `packages/harness/` 独立包，通过 workspace 管理
- **FastAPI lifespan 模式**：异步资源初始化（MCP 工具加载、graph 重建）
- **幂等启动**：`store.setup()` 自动建表，多次启动安全

---

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env：填入 GLM_API_KEY、TAVILY_API_KEY 等

# 3. 启动服务
uv run uvicorn app.main:fastApi --reload --host 0.0.0.0 --port 8000
```

## 技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | FastAPI |
| Agent 编排 | LangGraph（StateGraph + checkpointing） |
| LLM | GLM-5.1（通过 OpenAI 兼容 API） |
| 工具定义 | LangChain tools + MCP |
| 结构化提取 | TrustCall |
| 数据库 | PostgreSQL（Supabase），开发时可无数据库运行 |
| 向量检索 | Qdrant + Qwen text-embedding-v4 |
| 语音识别 | Groq Whisper |
| 网页搜索 | Tavily |
| 包管理 | uv（workspace monorepo） |

## 项目结构

详见 [AGENTS.md](AGENTS.md)。
