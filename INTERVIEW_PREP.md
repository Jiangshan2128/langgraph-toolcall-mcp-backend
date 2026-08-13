# AI Note Backend — 面试亮点速查手册（大连版）

> 依据 2026-08 大连地区 Agent / AI 岗位招聘简章实测优化
> 目标岗位：Agent 开发 / AI 产品经理
> 分支 `feature/app-context-di`（生命周期 DI 重构），代码以实况为准

---

## 0. 先看市场：大连岗位在要什么（30 秒）

综合 BOSS直聘/智联/前程无忧/猎聘/拉勾 大连岗位，共性技能按出现频次排序：

| 招聘关键词 | 出现频次 | 我们项目的匹配度 |
|---|---|---|
| **LangChain / LangGraph** | 几乎所有 Agent 岗 | ✅ 强匹配（图编排 + 子图 + HITL + checkpoint） |
| **工具调用 / Function Calling** | 高频 | ✅ 强匹配（9 核心工具 + MCP + 动态晋升） |
| **记忆持久化 / 状态记忆 / 长短期记忆** | 高频（高级岗明确要求） | ✅ 强匹配（PostgresStore + 会话 checkpoint 分层） |
| **流式输出 / SSE** | 高频（FastAPI 岗必提） | ✅ 强匹配（SSE + 轮询降级双方案） |
| **RAG（向量检索/重排序/知识库）** | 最高频之一 | ❌ 缺口（见 §5.1，必须诚实处理） |
| **多 Agent 协作** | 中高频 | ⚠️ 目前单 Agent（见 §5.2） |
| **MCP 协议** | 部分岗明确要求 | ✅ 强匹配（每用户 DingTalk MCP + schema 治理） |
| **Human-in-the-Loop** | 埃森哲等明确要求 | ✅ 强匹配（interrupt/resume 审批闭环） |
| **FastAPI 后端** | 高频 | ✅ 强匹配 |
| **Docker / 容器 / 云部署** | 高频 | ✅ 部分（Dockerfile + 微信云托管 serverless） |
| **模型微调（LoRA/SFT）** | 算法岗要求 | ❌ 未做（面 Agent 岗可不讲） |
| **日语（大连特色加分）** | 多家明确 | ⚠️ 看个人情况（见 §8） |

**核心结论**：你项目最强的四块——LangGraph 编排、工具调用、记忆持久化、流式输出——正是简章最高频的四项，**必须放最前面讲**。RAG 和多 Agent 是仅有的两块短板，本手册 §5 给了诚实话术和补课路径。

---

## 1. 一句话定位（30 秒开场）

> "这是一个**生产级、多租户、带人工审批闭环**的 AI 任务管理 Agent：后端用 LangGraph 编排，聊天 Agent 能长期记忆用户的任务/画像/偏好，改任务前先做人机确认，还能按用户维度按需接入钉钉 MCP 工具，并做了内容安全、鉴权、模型故障转移、serverless 部署适配。"

一句话里埋 5 个面试官会追问的词：**多租户 / 人工审批 / MCP / 内容安全 / 模型故障转移**。

---

## 2. 四大招聘核心技能 → 逐项对表（重点）

### 2.1 LangChain / LangGraph 编排

**招聘简章怎么写**（实测）：
> 埃森哲："精通 LangGraph/LangChain/AgentScope/Google-ADK，具备**工作流编排、工具链开发、多 Agent 协作及 Human-in-the-Loop 交互落地经验**"
> CSDC 审计："构建多 AI Agent 协作体系（任务分解、Tool Calling、长期记忆管理）"
> 外企德科："必须有 LangChain/LangGraph 实际项目经验，基于 FastAPI 实现高并发接口、SSE 流式输出"

**项目里怎么体现**（代码）：
- 主图 4 节点 + 3 条条件路由：`builder.py:122-160`（`build_graph`）、`routing.py`
  - 有音频 → 转录子图；agent 调工具 → tools；tools 出任务提案 → hitl_node 审批
- **子图编排**：转录子图有自己的 private state（`transcription/graph.py`），图套图——这是 LangGraph 子图隔离状态的实战
- **HITL 落地**：`interrupt()` 暂停 + `Command(resume=decision)` 恢复（`nodes.py`、`service.py:242-253`）
- **checkpoint / 会话**：`thread_id = user:session`（`app/chat/thread.py`），MemorySaver 存会话、PostgresStore 存业务记忆
- 新 API 用对：`ainvoke(version="v2")` 拿 `GraphOutput`、`astream_events(version="v3")` 流式（`service.py:90-104,164-196`）

**面试怎么讲**：
> "Agent 的本质是**决策循环**。我用 LangGraph 把它显式建模成状态机：节点只干一件事，条件边负责决策。这样每一步**可观测、可恢复、可插入人工节点**——比裸写 while 循环调 LLM 强在 checkpoint 能存中间状态、interrupt 能暂停、resume 能续跑。子图隔离状态我也有实战（音频转录子图用独立 state，不污染主图的对话消息）。"

**面试官会深挖**：为什么 LangGraph 不用 AutoGen/CrewAI → 状态机 vs 多智能体对话，单 Agent 高可靠控制流场景 LangGraph 最贴合，且 interrupt/checkpoint 原语成熟。
多线程会话怎么隔离 → `user_id:session_id` 拼 thread_id，换账号不串线，换 session 开新对话（正好控制上下文窗口长度）。

---

### 2.2 工具调用（Tool Calling / Function Calling）

**招聘简章怎么写**（实测）：
> CSDC："任务分解、**Tool Calling**、长期记忆管理"
> 外企德科："封装大模型调用与**工具函数调用（Function Calling）**及向量搜索"
> 天恩璐："任务拆解、**工具调用**、记忆机制、多轮对话"
> 医药研发 Agent 岗："动态技能调用机制……实现任务自动拆解、规划与**工具调用**"

**项目里怎么体现**（代码）：
- **9 个核心工具**统一在 `ALL_TOOLS`（`tools/__init__.py`）：update_profile / update_tasks / update_instructions / web_search / get_tasks_tool / mark_task_done / update_task_priority / delete_task_by_title / get_current_time
- **工具注入**：`InjectedState()` / `InjectedStore()` 把图状态和存储直接注入工具（`tools/core/memory.py:121-125`），工具天然多租户
- **绑定与执行分离**：`tool_binder.py` 决定"哪些工具给 LLM"，`ScopedToolNode` 决定"工具在哪执行"（按 user_id 路由到该用户的 ToolNode，`scoped_tool_node.py:49-90`）
- **工具爆炸的解法**：核心工具常驻 + `tool_search` 按需拉取 MCP 工具 schema 并晋升到 `promoted_tools`（`tool_binder.py:122-209`、`tool_search.py:141-199`）
- **不可信 schema 治理**：绑定时清洗非法 JSON Schema（"Map<String, Any>" → object、错位 required 上提），一个坏工具不拖垮整轮（`tool_binder.py:26-120`）
- **结构化抽取（TrustCall）**：`update_tasks`/`update_profile` 用 create_extractor + 确定性 `json_doc_id`（`tools/core/memory.py:141-242`），工具返回的是**提案 JSON**而非直接写库 → 交给 HITL

**面试怎么讲**：
> "工具调用我做了三层：**绑定层**决定给模型哪些工具（核心常驻 + 按需晋升），**执行层**决定在哪跑（每用户一个 ToolNode，隔离凭据），**schema 治理层**清洗第三方 MCP 的不合规 Schema。另外写操作工具走 TrustCall 结构化抽取，返回提案而不是直接写库——模型只提方案，写不写由人工审批决定。"

**面试官会深挖**：工具太多怎么办 → 三层绑定 + deferred；工具执行出错会不会拖垮整个 Agent → 绑定层跳过坏 schema、错误处理中间件兜底；用户凭据隔离 → ScopedToolNode 按 user 解析。

---

### 2.3 记忆持久化（Memory / 长期记忆 / 状态记忆）

**招聘简章怎么写**（实测）：
> AI agent 专家岗（30-60k·16薪）："设计分布式可扩展的 **Agent Memory 体系**，实现**长短期记忆管理、上下文保持和知识沉淀机制**"
> 信华信："优化**状态记忆**、调度策略等技术环节"
> 医药研发 Agent 岗："构建**长期记忆模块**与动态技能调用机制"
> 埃森哲："**对话历史管理**、对话历史压缩"等上下文工程手段

**项目里怎么体现**（代码）：
- **长期记忆（业务数据）**：PostgresStore（Supabase）持久化，按用户 namespace 分域——`profile` / `task` / `instructions` / `dingtalk`（`memory.py:10-12`）
- **会话记忆（短期）**：MemorySaver checkpoint 存对话状态，`thread_id` 区分会话（`builder.py:38-98` 的 `create_runtime` 返回）——"业务记忆可持久、会话状态可轻量"
- **每轮注入系统提示**：`MemoryLoadMiddleware` 每轮从 store 读画像/任务/指令 → `SystemPromptMiddleware` 拼进系统提示（`middleware/memory_load.py`、`middleware/system_prompt.py`）→ Agent "越用越懂你"
- **结构化写入**：TrustCall 抽取画像/任务/指令，`json_doc_id` 确定性去重防重复记忆；任务模型带优先级 P0/P1/P2、时间、周期 recurrence、状态（`tools/core/memory.py:39-112`）
- **容错**：`Task.time` 宽松日期解析，LLM 输出不规范不炸整列表（`tools/core/memory.py:63-96`）

**面试怎么讲**：
> "记忆我按**长短期分层**：长期记忆是业务数据——任务、画像、规划偏好，落 PostgresStore，重启不丢；短期记忆是会话状态，走 checkpoint，按 thread_id 隔离。每轮对话前把记忆读出来拼进系统提示，Agent 才知道你是谁、有什么任务。写入用结构化抽取保证字段干净，用确定性 key 防止重复。"

**面试官会深挖**：为什么业务记忆不塞 checkpoint → checkpoint 是会话粒度、且是轻量的，业务记忆要跨会话跨重启存在，且要按用户查。多用户数据隔离 → store namespace `(前缀, user_id)`。记忆写坏怎么办 → Pydantic 校验 + 宽松解析 + 人工审批。

---

### 2.4 流式输出（SSE / Streaming）

**招聘简章怎么写**（实测）：
> 外企德科："基于 FastAPI 实现高并发接口、**SSE 流式输出**"
> 埃森哲：RAG 全流程 + 上下文工程（流式是大模型交互标配）
> 亚信："FastAPI/gRPC 接口开发"

**项目里怎么体现**（代码）：
- **SSE 通道**：`sse_starlette.EventSourceResponse`（`router.py:105-138`）
- **token 级流式**：`astream_events(version="v3")` 逐 token 吐 `stream.messages`（`service.py:164-176`）
- **事件协议**：`connected`（先推防代理超时）→ `message`（逐 token）→ `interrupt`（HITL 审批卡片）→ `tasks`（流结束后推完整任务列表）→ `done` / `error`（`service.py`）
- **流式中断检测**：`stream.interrupted()` / `stream.interrupts()` 干净拿到审批 payload，不 hack `get_state()`（`service.py:179-196`）
- **serverless 降级**：微信云托管跑不了长连接 → **轮询 jobs API**，后台 asyncio task 跑图、interrupt 时阻塞等 resume 事件、多轮 HITL 天然支持（`app/jobs/runner.py:22-20`，超时/孤儿 job 都有兜底）

**面试怎么讲**：
> "流式做了两条路：常驻服务走 **SSE token 级流式**，事件协议里把 HITL 中断作为一等事件推给前端渲染审批卡片；微信云托管这种 serverless 跑不了长连接的场景，我做了**轮询降级**——后台任务跑图、状态落库、前端轮询，interrupt 时任务挂着等人审批再唤醒。两条路覆盖了'能流式'和'不能流式'两种部署。"

**面试官会深挖**：v2/v3 流式 API 区别 → v3 的 stream.messages/interrupted 比旧版更干净；中断后怎么续 → 单独 POST /chat/resume 带同 session_id；流式中断的 token 怎么处理 → 已吐的保留，前端看到审批卡片。

---

## 3. 生产化加分项（讲完四大核心再补）

| 维度 | 具体点 | 代码 |
|---|---|---|
| **架构** | Agent 节点用**中间件管道**（俄罗斯套娃）拆关注点：错误处理→记忆加载→系统提示→工具绑定→LLM | `middleware/base.py` |
| **架构演进（DI）** | 长生命周期组件改为 **lifespan 托管的 DI 容器**（`app.state.app_context` + `Depends` 访问器）；import 零副作用——不再 import 即连库/编译图/画图；测试可注入内存 store。参考了社区开源项目 **DeerFlow** 的 `langgraph_runtime` + `_require()` 模式 | `common/container.py`、`common/dependencies.py:138-202`、`builder.py:1-13` |
| **可用性** | 连接池启动即验证读写，宁可启动失败也不静默回退内存丢数据；健康检查真实读写 store | `builder.py:63-93`、`main.py:74-99` |
| **稳定性** | 模型层多 Provider 配置 + **瞬时错误自动 failover**（5xx/429），非瞬时 4xx 故意不切防掩盖 bug；思考模式分离（推理模型禁 tool_choice="required"） | `config/model_factory.py`、`config/model_failover.py` |
| **合规** | 用户输入过**微信 msgSecCheck**，AI 回复再过滤，命中风险换占位文案；fail-open（审核服务挂了不卡用户） | `content_safety.py` |
| **安全** | Supabase JWT 验签（ES256/JWKS）；`user_id` 永不信任请求体；全局异常不回传 `str(exc)` 防泄漏；账号删除（合规） | `dependencies.py:50-96`、`main.py:46-59` |
| **部署** | Docker + 微信云托管（serverless）部署；Dockerfile / docker-start.bat | 仓库根目录 |
| **输入治理** | 音频 20MB 上限防 DoS；日期宽松解析兜底 | `dependencies.py:102-134` |
| **测试** | 17 个测试文件覆盖路由/节点/绑定/运行时/OAuth/内容安全 | `tests/` |

---

## 4. 面试故事线 —— 把 demo 讲成"你解决过真问题"

面试官最想听的是**你踩过的坑和取舍**。每条都带"因为踩过……"：

1. **"工具太多模型炸了"** → deferred + tool_search 动态晋升（§2.2）
2. **"钉钉 MCP 输出非法 schema 导致整轮 400"** → schema 清洗（§2.2）
3. **"模型服务 503 就全挂"** → failover 且区分瞬时/非瞬时（§3）
4. **"改任务前模型直接写库，用户被坑"** → HITL 审批闭环（§2.1）
5. **"全局启用钉钉，用户 A 凭据污染用户 B"** → 每用户运行时（§2.2）
6. **"微信云托管跑不了长连接"** → 轮询 jobs API（§2.4）
7. **"Supabase 掐空闲连接，池子借出死连接"** → 连接池 ping 自愈 + 生命周期（`builder.py:63-80`）
8. **"模块级单例 import 时就连库，测试一跑就炸、组件没法替换"** → 生命周期容器 + `Depends` 声明式注入，import 零副作用；DingTalk 运行时改持有注入 store 的类实例（§3、§6）

---

## 5. 诚实处理两个缺口（重要，面试前必读）

### 5.1 RAG —— 简章最高频、项目最缺

**现实**：大连几乎所有 AI 岗都写 RAG（文档解析→向量化→检索→重排序，Milvus/Pinecone/Chroma/FAISS）。我们的项目**没有实现**。

**话术（诚实版，主动说）**：
> "RAG 是我的**设计目标**，目前 Agent 走的是工具调用 + 结构化记忆路线；我准备用 Qdrant + 混合检索（稠密+稀疏）把知识库能力接进来——文档上传、切块、向量化、检索后注入系统提示。这块我在 LangChain 的架构上已经预留了插入点。"

**更好的选择（推荐）**：面 RAG 岗之前，花 1-2 天在项目里加一个**最小 RAG demo**（Qdrant 或 Chroma + embedding → 检索 → 拼进系统提示），简历和现场演示就都有实锤。**需要的话我可以帮你实现**（见文末）。

### 5.2 多 Agent 协作 —— 目前单 Agent

**现实**：埃森哲、CSDC、商本 DeepResearch 都明确要多 Agent。

**话术（诚实 + 讲底层能力）**：
> "当前是单 Agent + 工具编排，但我的架构已经具备多 Agent 的底层：LangGraph 的**子图隔离**我用了（转录子图），**并行分支**和**图套图**是同一套机制。下一步做主管/协作者多 Agent（规划 Agent 拆任务 → 执行 Agent 调工具 → 汇总 Agent 收口）可以直接在现有图上加节点，不需要换框架。"

### 5.3 模型微调（LoRA/SFT）—— 算法岗才要

面 **Agent 开发 / 产品经理**不硬性；若面算法岗，诚实说"未做微调，专注应用层"。别硬吹。

---

## 6. 高频面试题 + 你该答什么

**Q：这个 Agent 是怎么跑起来的？** → 4 节点 + 3 条件路由 + 中间件管道，30 秒版本（§2.1）。

**Q：为什么选 LangGraph？** → 状态机 vs 裸循环；interrupt/checkpoint 原生支持 HITL；子图隔离状态（§2.1）。

**Q：遇到过最难的 bug？** → 推荐"钉钉 schema 清洗导致整轮 400"或"HITL resume key 串号"，讲真细节（§2.2、§4）。

**Q：怎么保证 Agent 不乱改数据？** → 写操作过 HITL 审批、读操作不过；TrustCall 结构化抽取 + 确定性 key（§2.2、§2.3）。

**Q：多用户怎么隔离？** → 每用户运行时注册表 + ScopedToolNode 按 user_id 路由 + store namespace 隔离（§2.2）。

**Q：工具太多怎么办？** → 三层绑定 + tool_search 延迟装载（§2.2）。

**Q：模型幻觉怎么办？** → 结构化抽取兜底 + Pydantic 枚举/日期校验 + 宽松解析不炸列表 + 内容安全 + 写操作人工审批（§2.3、§3）。

**Q：加一个新工具要改哪些地方？** → `tools/` 定义 → 加进 `ALL_TOOLS` → 写操作配 HITL（routing + nodes）。扩展点清晰。

**Q：为什么不用模块级单例（import 就连库）？依赖怎么管理？** → 这是我对项目做的**架构重构**：长生命周期组件（DB 连接池、store、checkpointer、编译后的 graph、DingTalk 运行时）全部收进 FastAPI **lifespan** 的 `create_app_context()` 里创建，挂在 `app.state.app_context`，路由用 `Depends` 声明式取用（`StoreDep`/`GraphDep`）。收益：**import 零副作用**（不连库、不编译图、不画图，单测也能跑）、**组件可替换**（测试注入内存 store）、**关闭顺序对称**（finally 逆序 close）。图内部节点跑在请求作用域外，用「模块级指针 → 运行时实例」的方式访问（DeerFlow 的 `get_local_provider` 同款模式）。**加分句**："这个架构我参考了社区开源项目 DeerFlow 的做法，先对比了它的 `langgraph_runtime` 再动手。"

**Q：性能/成本考虑？** → MCP 不启动加载省冷启动；工具延迟装载省 token；前端 session_id 控制上下文长度；failover 保可用性（§2.4、§3）。

**Q：RAG 怎么设计？**（如果投 RAG 岗，这是必问题）→ 讲标准链路：文档解析→分块→embedding→向量库→检索（混合检索+重排）→注入上下文。诚实说明本项目未落地，讲清你会在哪里插入（§5.1）。

---

## 7. AI 产品经理视角（用大连 PM 岗位实测）

大连 PM 岗（达晨、高新园区大厂、安复仕、迈思诚等）高频要求：**Prompt 工程优化、AI 交互流程设计、AI 产品评测体系（标注/Badcase 分析）、控制 AI 幻觉、设计 RAG/Agent/Workflow 方案、合规与安全设计、PRD 含算法逻辑、与算法/数据工程师协作**。逐条对到你的项目：

| PM 岗要求 | 你项目的对应故事 |
|---|---|
| AI 交互流程设计 | 你设计了**多轮会话 + 审批卡片的 HITL 交互**——"AI 改数据前先问用户"，这是产品级信任设计 |
| 控制 AI 幻觉 | 结构化抽取（TrustCall）+ Pydantic 校验 + 写操作审批 + 内容安全过滤——**幻觉的工程防线**你能讲透 |
| 设计 Agent/Workflow 方案 | 你能画出**图结构（4 节点 + 条件路由）**，还会讲为什么这样编排 |
| Prompt 工程 | 系统提示由记忆动态拼装（`system_prompt.py`），deferred 工具提示区（`tool_search.py`）——真实 Prompt 工程经验 |
| 评测体系（标注/Badcase） | 诚实说"我靠 debug_utils 的打印 + 测试文件做回归"；可以讲你会怎么搭 Badcase 集（把 HITL 误判、工具误调用样本沉淀成回归用例） |
| 合规与安全设计 | 微信 msgSecCheck + 回复过滤 + 账号删除 + JWT 鉴权——国内上架 AI 产品的合规红线你全踩过 |
| 协作能力 | 你和前端对接 SSE 事件协议、和甲方对接钉钉 OAuth 凭据——跨端协作实例 |

**PM 高频追问**："这个产品你怎么迭代？" → 主动提醒（到期推任务）、跨应用编排（日历+任务+消息联动）、任务审批通过率/任务创建准确率埋点衡量。

---

## 8. 大连本地特色 & 面试实用技巧

- **日语是大连明确的加分项**：创未科技、外企德科、中软国际（对日售前 22-50k）、迈思诚都要求/加分。**会就写进简历，不会就别主动提**。
- **大连 AI 岗底色是对日/IT 服务**：埃森哲、东软、中软国际、亚信、德科是大户——它们更看重**落过真实项目、能直接干活**，demo 要讲成"解决了真实故障的系统"。
- **部分岗要现场演示/现场编程**（天恩璐：面试需项目演示或现场编程）。**HITL 审批流程是最佳演示点**——先问个"帮我加个任务"，展示审批卡片出现 → 前端批准 → 任务落库 → Agent 确认。
- **薪资锚点**（谈薪时心里有数）：Agent 开发 1.4-2.1 万/月（CSDC）、2-4 万（天恩璐）、AI agent 专家 30-60k·16薪；PM 8k-15k，资深（对日）22-50k。
- **警惕培训陷阱**：某 AIGC 岗（3-5k）要求先免费培训两月考核上岗——明显是培训招生，别去。

---

## 9. 去面试前的行动清单

1. **把 §2 四个核心技能的代码路径背下来**，讲的时候直接说"你看 `tool_binder.py` 这里……"
2. **跑通 HITL 审批 demo**（最有说服力），录个屏备用
3. **RAG 缺口二选一**：诚实话术（§5.1）或补一个最小 demo（推荐，1-2 天）
4. **投递前对照 JD**：JD 里出现的关键词，逐条想好"我的项目哪个点对应它"
5. **备好 1 分钟自我介绍**：一句话定位（§1）+ 四大核心（§2）+ 一个真实故障（§4）

---

## 招聘信息来源（大连，2026-08）

- [埃森哲 AI 全栈工程师（AI Agent 方向）](https://www.accenture.com/cn-en/careers/jobdetails?id=14477303_en)
- [CSDC AI Agent 开发工程师（智联）](https://www.zhaopin.com/jobdetail/CC654313220J40872098509.htm)
- [智能体开发技术专家（51job）](https://jobs.51job.com/dalian/167567195.html)
- [Python+AI 工程师（猎聘）](https://www.liepin.com/job/1981087035.shtml)
- [AI Agent 开发工程师（智联，中山区）](https://www.zhaopin.com/jobdetail/CCL1483449660J40786146809.htm)
- [大模型应用工程师 Agent 方向（猎头）](https://gobasearcher.com/job/detail-13819.html)
- [大模型算法应用工程师（大连最大 AI 团队）](https://myjob.dlmu.edu.cn/job/view/id/1916725)
- [AI agent 专家-DL（猎聘）](https://m.liepin.com/a/76001027.shtml)
- [短期项目 Python Web 服务端（AI 方向，SSE/流式）](https://www.yupao.com/zhaogong/413607562/A78idDcpTrmZY6Nf8mz9qljOpm.html)
- [AI 产品经理（达晨科技）](https://myjob.dlmu.edu.cn/job/view/id/1917672)
- [AI 产品经理（高新园区）](https://www.zhaopin.com/jobdetail/CC630290130J40972642902.htm)
- [对日 AI 售前/产品经理/开发（猎聘）](https://m.liepin.com/job/1984282505.shtml)
- [AI Agent 工程师（医药研发智能体，猎聘）](https://m.liepin.com/a/78707611.shtml)
- [AI 应用平台开发工程师（智联）](https://www.zhaopin.com/jobdetail/CC852808000J40911877508.htm)
