# Banana Todo List Backend - AGENTS.md

## Project Overview

Banana Todo List Backend is a FastAPI-based agent service with a two-layer architecture:

```
app/                          <- Web application layer (FastAPI)
└── chat/                     <- Chat & task REST APIs

packages/harness/ainote/      <- Agent/LLM core layer
├── agents/                   <- LangGraph graph, models, prompts, memory
├── tools/                    <- Agent tools (memory CRUD, search, DingTalk MCP)
├── config/                   <- App/model/database/tool settings
└── transcription/            <- Audio/video transcription subgraph
```

Key capabilities:
- **Task Management**: Long-term memory for user tasks (CRUD via both chat agent and REST API)
- **Human-in-the-Loop (HITL)**: Interactive approval workflow before task mutations
- **DingTalk Integration**: MCP tools for DingTalk office suite operations
- **RAG Knowledge Base**: Qdrant + Qwen text-embedding-v4 (dense+sparse hybrid search)
- **GraphRAG**: Entity-relationship knowledge graph for global overviews and multi-hop reasoning
- **Transcription**: Audio/video transcription via Groq Whisper

## Architecture

```
                   FastAPI Layer (app/)
  ┌─────────────┐  ┌──────────────────┐
  │ chatRouter  │  │ taskRouter       │
  └──────┬──────┘  └───────┬──────────┘
         │                 │
         ▼                 ▼
           LangGraph Agent (packages/harness)
  ┌─────────┐   ┌─────────┐   ┌─────────┐
  │  START  │──▶│  agent  │──▶│  tools  │
  └─────────┘   └─────────┘   └────┬────┘
                                    │
                             ┌──────▼──────┐
                             │  hitl_node  │
                             └──────┬──────┘
                                    │
                             ┌──────▼──────┐
                             │   store     │
                             └──────┬──────┘
                                    ▼
                               ┌─────────┐
                               │   END   │
                               └─────────┘
```

## Project Structure

```
├── app/                                  <- Web layer
│   ├── main.py                           <- FastAPI entry + sys.path setup
│   ├── chat/
│   │   ├── router.py                     <- Chat endpoints (POST /chat, /chat/stream, /chat/resume)
│   │   ├── service.py                    <- ChatService class: LLM invocation, streaming, resume (store/graph injected)
│   │   ├── schemas.py                    <- ChatRequest, ChatResponse, ResumeRequest, TaskUpdateRequest
│   │   ├── task_router.py               <- Task REST endpoints (DELETE/PATCH /tasks/{key})
│   │   └── task_service.py              <- Task CRUD via store (out-of-band mutations)
│   └── common/
│       ├── container.py                 <- AppContext DI container: creates store/graph/dingtalk in lifespan
│       └── dependencies.py              <- FastAPI Depends accessors (StoreDep/GraphDep from app.state.app_context)
│
├── packages/harness/ainote/              <- LLM/Agent core
│   ├── agents/
│   │   ├── debug_utils.py               <- HITL debug printing utilities
│   │   └── graph/
│   │       ├── __init__.py              <- Lazy re-exports (avoid circular imports)
│   │       ├── prompts.py               <- System prompt, TrustCall instruction, etc.
│   │       ├── memory.py                <- Store access layer (profile, tasks, instructions)
│   │       ├── model/
│   │       │   ├── model.py             <- Configuration, get_model() (config-driven, failover)
│   │       │   ├── model_factory.py     <- config.yaml multi-provider factory (fallback chains)
│   │       │   ├── model_failover.py    <- FailoverChatModel (transient-error failover)
│   │       │   └── model_provider.py    <- ModelProvider/ModelConfigYAML pydantic models
│   │       ├── builder.py               <- Pure factories: create_runtime(), build_graph() (no import-time side effects)
│   │       ├── fault_tolerance.py       <- Graph-level retry/timeout/error-handler config (retry_on, RETRY_POLICY, graph_error_handler)
│   │       ├── dingtalk_runtime.py      <- Per-user DingTalk MCP runtime registry (class owning injected store)
│   │       ├── state.py                 <- AgentState (messages, user_id, metadata, audio)
│   │       ├── tool_binder.py           <- get_model_with_tools(): core + promoted MCP tool binding
│   │       └── nodes/
│   │           ├── __init__.py          <- Re-exports agent_node, hitl_node
│   │           ├── agent_node.py        <- agent_node (middleware pipeline) + pipeline factory/singleton
│   │           ├── hitl_node.py         <- hitl_node (interrupt/approval)
│   │           ├── routing.py           <- Conditional edges: route_start, route_after_agent, route_after_tools
│   │           ├── scoped_tool_node.py  <- ScopedToolNode (per-user-scoped tool execution)
│   │           └── middleware/
│   │               ├── base.py          <- Middleware protocol, Pipeline (Russian-doll pattern)
│   │               ├── memory_load.py   <- Load profile/tasks/instructions from store
│   │               ├── system_prompt.py <- Build system prompt with memories + deferred tools
│   │               └── tool_binding.py  <- Bind ChatOpenAI with tool list via tool_binder
│   │
│   ├── tools/
│   │   ├── __init__.py                 <- ALL_TOOLS list (9 core tools) + remove_tools_by_name
│   │   ├── core/
│   │   │   ├── memory.py               <- update_profile, update_tasks, update_instructions (TrustCall)
│   │   │   │                             Also defines Profile & Task Pydantic models
│   │   │   ├── tasks.py                <- get_tasks, mark_task_done, update_task_priority, delete_task_by_title
│   │   │   ├── time.py                 <- get_current_time
│   │   │   ├── mcp_loader.py           <- Load MCP server tools from mcp_servers.json
│   │   │   └── tool_search.py          <- Deferred DingTalk MCP tool search & promotion
│   │   └── community/
│   │       ├── search.py               <- web_search (Tavily)
│   │       └── __init__.py             <- Re-exports web_search + load_mcp_tools
│   │
│   ├── config/
│   │   ├── __init__.py                 <- Re-exports from focused config modules
│   │   ├── settings.py                 <- Unified settings proxy (delegates to sub-modules)
│   │   ├── app_config.py               <- FastAPI app title, version, etc.
│   │   ├── model_config.py             <- LLM provider settings (GLM_API_KEY, etc.)
│   │   ├── database_config.py          <- DATABASE_URL, etc.
│   │   └── tool_config.py              <- Tavily, MCP, etc.
│   │
│   └── transcription/
│       ├── graph.py                    <- Transcription subgraph (Groq Whisper)
│       ├── service.py                  <- _transcribe, get_groq_client
│       ├── _ffmpeg.py                  <- Audio splitting utilities
│       └── schemas.py                  <- Transcription input/output models
│
├── tests/
├── packages/harness/__init__.py
├── pyproject.toml
├── mcp_servers.json                    <- MCP server configuration (DingTalk, RAG tools)
└── AGENTS.md
```

## Key Components

### 1. Agent Graph (`packages/harness/ainote/agents/graph/`)

| File | Purpose |
|------|---------|
| `builder.py` | Pure factories: `create_runtime()` → `(store, checkpointer, pool)`; `build_graph(store=, checkpointer=)`. No module globals / import-time side effects |
| `memory.py` | Store access layer: profile/tasks/instructions/dingtalk namespaces |
| `prompts.py` | System prompt, TrustCall instruction, etc. |
| `model/model.py` | `Configuration` (runtime config) + `get_model()` (config-driven, with failover) |
| `model/model_factory.py` | config.yaml multi-provider factory: `create_model` / `create_model_with_failover`, provider registry, `fallback_to` chains (cycle-safe) |
| `model/model_failover.py` | `FailoverChatModel`: transient-error failover over a model chain (retries 5xx/429/timeout; 4xx propagate) |
| `model/model_provider.py` | `ModelProvider` / `ModelConfigYAML` pydantic models (incl. `fallback_to` cycle validation) |
| `dingtalk_runtime.py` | `DingTalkRuntime` class owning the injected store + per-user registry; module-level `configure_runtime()`/`get_runtime()` pointer for graph internals |
| `state.py` | AgentState definition (messages, user_id, metadata, audio) |
| `tool_binder.py` | `get_model_with_tools()`: selects core + promoted MCP tools to bind to LLM |
| `nodes/agent_node.py` | `agent_node` (middleware pipeline) + pipeline factory / lazy singleton |
| `nodes/hitl_node.py` | `hitl_node` with interrupt/approval logic |
| `nodes/routing.py` | Conditional edges: transcription -> agent -> tools -> HITL -> END |
| `nodes/scoped_tool_node.py` | `ScopedToolNode`: per-user tool routing at the tools node |
| `nodes/middleware/` | Agent-node middleware pipeline (see section 2) |

The lifespan-managed DI container (`app/common/container.py`) calls the factories
once at startup and exposes the results on `app.state.app_context`; request
handlers resolve them via `Depends` getters in `app/common/dependencies.py`
(`StoreDep`, `GraphDep`, …). Chat endpoints never take `store`/`graph` directly
in their signature — `app/chat/service.py` wraps them in a `ChatService` class,
and handlers declare `svc: ChatServiceDep`
(`Annotated[ChatService, Depends(get_chat_service)]`), so store/graph acquisition
is fully in the service layer. Graph-internal call sites run outside request
scope and use the `dingtalk_runtime` module-level pointer instead.

### 2. Agent Middleware Pipeline (`agents/graph/nodes/middleware/`)

The agent node uses a Russian-doll middleware pipeline for separation of concerns.
The middlewares are pure "prepare" layers — fault tolerance (retry/timeout/error
handling) lives at the graph level, not in the pipeline:

```
MemoryLoadMiddleware        <- load profile/tasks/instructions from store
  └── SystemPromptMiddleware  <- build system prompt from memories
      └── ToolBindingMiddleware  <- bind tools to ChatOpenAI
          └── core_handler  <- LLM invocation
```

### 3. Tools (`packages/harness/ainote/tools/`)

| Tool | Purpose | HITL? |
|------|---------|-------|
| `update_tasks` | Create/update tasks (TrustCall extractor) | ✅ Yes |
| `update_profile` | Update user profile (TrustCall extractor) | ❌ No |
| `update_instructions` | Update planning preferences | ❌ No |
| `get_tasks` | List tasks from store | ❌ No |
| `mark_task_done` | Complete a task by title | ❌ No |
| `update_task_priority` | Change task priority | ❌ No |
| `delete_task_by_title` | Delete a task by title | ❌ No |
| `web_search` | Web search via Tavily | ❌ No |
| `dingtalk_*` | DingTalk MCP tools (lazy-loaded) | ❌ No |

### 4. Human-in-the-Loop (HITL)

**Flow:**
```
1. LLM calls update_tasks -> TrustCall extracts proposed changes
2. Tool returns proposals JSON (type: "task_proposals") — no store write
3. route_after_tools -> hitl_node
4. hitl_node calls interrupt() with proposal payload
5. Frontend shows UI for approve/edit/reject
6. User submits -> resume() with approval data
7. hitl_node applies approved/edited changes to store
8. Edge back to agent for acknowledgment
```

**Key Design:**
- Only `update_tasks` uses HITL (modifies user task data)
- Read-only tools (`get_tasks`, `web_search`) skip HITL
- Proposals survive resume via checkpointed ToolMessage
- Deterministic keys (json_doc_id from TrustCall) prevent key mismatch on resume

### 5. Tool Binding (`agents/graph/tool_binder.py`)

**Problem:** GLM-5.1 has ~55 tool limit, but DingTalk MCP adds ~98 tools.

**Solution:** Three-tier binding in `get_model_with_tools()`:
1. **Core tools** (8) always bound
2. **`tool_search`** always bound (if in ALL_TOOLS)
3. **Promoted MCP tools** bound on-demand via state["promoted_tools"]

Core + promoted selection is done inside `get_model_with_tools()` — there is no separate router module.

### 6. Fault Tolerance (`agents/graph/fault_tolerance.py`)

LangGraph-level fault tolerance (needs `langgraph>=1.2`; project locks 1.2.2), configured
once in `builder.py::build_graph`:

- **Retries** — `set_node_defaults(retry_policy=RetryPolicy(max_attempts=3, retry_on=retry_on))`
  re-runs a failed node on transient errors with exponential backoff. The custom
  `retry_on` extends `default_retry_on`: it additionally retries `TimeoutError`
  (an OSError subclass the default excludes) and makes OpenAI/Groq-style
  `APIStatusError` status-aware (retry only 429/5xx; 4xx are fatal).
- **Timeouts** — per-node `TimeoutPolicy` (async nodes only): `agent` 300s/120s,
  `tools` 240s/90s, `transcription` 600s/180s. `idle_timeout` resets on progress
  signals (LLM tokens, tool callbacks), so long but productive work survives.
- **Error handling** — a shared `graph_error_handler` runs when a node raises a
  *non-retryable* error (immediately — no retries are spent) or exhausts its
  retries, and returns a friendly user message (logged server-side, never leaks
  exception detail) instead of bubbling a 500 to the HTTP layer. It
  distinguishes fatal model bad-requests (400 / `BadRequestError` →
  provider-specific apology) from generic failures (generic apology).

There is **no `ErrorHandlingMiddleware` in the agent-node pipeline** anymore:
all error handling lives at the graph level so there is a single fault-tolerance
path. The middlewares (`MemoryLoad` / `SystemPrompt` / `ToolBinding`) are pure
"prepare" layers — any exception they raise propagates out of `agent_node` to the
graph runtime, where `retry_on` decides retry-vs-fatal and `graph_error_handler`
produces the final user message.

Notes: `hitl_node`'s `interrupt()` bypasses retry/timeout/error-handler, so the
defaults are safe there. Tool *logic* errors never reach node-level retry
(`ToolNode.handle_tool_errors=True` feeds them back to the LLM). Defaults set on
the parent graph are NOT inherited by the transcription subgraph — the parent
`transcription` node wraps the whole subgraph run.

### 7. Configuration

**Environment Variables (`.env`):**
```env
# LLM Provider (required)
GLM_API_KEY=your_key
GLM_MODEL=glm-5.1
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/

# Database (optional, fallback to in-memory)
DATABASE_URL=postgresql://user:pass@localhost/db

# DingTalk MCP (optional)
DINGTALK_MCP_ENABLED=true
DINGTALK_APP_KEY=your_key
DINGTALK_APP_SECRET=your_secret

# Web Search
TAVILY_API_KEY=your_key

# Transcription (optional)
GROQ_API_KEY=your_key
```

## Development Guidelines

### Adding a New Tool

1. Define tool function in `packages/harness/ainote/tools/` (add new file or extend existing)
2. Add to `ALL_TOOLS` in `packages/harness/ainote/tools/__init__.py`
3. If it modifies user task data, add HITL workflow (see below)

### Adding HITL to a Tool

1. Tool should return proposals JSON (not write to store directly)
2. Add routing logic in `route_after_tools()` in `routing.py`
3. Add parsing logic in `_parse_task_proposals()` in `nodes/hitl_node.py`
4. Add summary builder in `build_hitl_summary()` in `debug_utils.py`

### Debug Printing

```python
from ainote.agents.debug_utils import print_proposed_tasks, print_approval_result

print_proposed_tasks(proposed)
print_approval_result(approval, rejected_keys, edited_tasks)
```

### Import Convention

Code inside `packages/harness/ainote/` uses `from ainote.xxx import` (not `from app.xxx`).
Code inside `app/` imports from `ainote` for agent/LLM modules, `app` for web-only modules.

`main.py` adds `packages/harness` to `sys.path` at startup:
```python
sys.path.insert(0, str(Path(__file__).resolve().parent / "packages" / "harness"))
```

## Common Issues

### 1. GLM-5.1 "API 调用参数有误" (Error 1210)
**Cause:** Too many tools (>55) sent to model via bind_tools
**Fix:** Ensure DingTalk MCP tools are deferred (not core); they get bound via `tool_search` promotion, not all at once.

### 2. HITL Resume Key Mismatch
**Cause:** Node restarts on resume, TrustCall regenerates json_doc_ids
**Fix:** TrustCall returns deterministic `json_doc_id` from metadata; comparison uses checkpointed ToolMessage, not re-invocation.

### 3. Tool Not Available
**Cause:** Tool not in `ALL_TOOLS` or filtered by binding strategy
**Fix:** Check `packages/harness/ainote/tools/__init__.py` and tool_binder logs.

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_graph.py -v
```

## Key Dependencies

- **LangGraph**: Agent orchestration with checkpointing
- **LangChain**: LLM abstractions, tool definitions
- **TrustCall**: Structured extraction for task/profile updates
- **FastAPI**: HTTP API framework
- **PostgreSQL** (optional): Persistent checkpoint and memory store
- **MCP**: Model Context Protocol (DingTalk tools, RAG tools)
- **Groq Whisper**: Audio transcription
- **Tavily**: Web search
