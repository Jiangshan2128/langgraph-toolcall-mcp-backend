# AI Note Backend - Claude Code Guide

## Project Overview

AI Note Backend is a FastAPI-based agent service that provides:
- **Task Management**: Long-term memory for user tasks with CRUD operations
- **Human-in-the-Loop (HITL)**: Interactive approval workflow for task updates
- **DingTalk Integration**: MCP tools for DingTalk office suite operations
- **Multi-Model Support**: Configurable LLM providers (OpenAI, GLM, etc.)
- **Transcription**: Audio/video transcription subgraph

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FastAPI Layer                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ chatRouter  │  │ taskRouter  │  │    transcription        │  │
│  └──────┬──────┘  └──────┬──────┘  └─────────────────────────┘  │
└─────────┼────────────────┼──────────────────────────────────────┘
          │                │
          ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      LangGraph Agent                             │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────────┐  │
│  │  START  │───▶│  agent  │───▶│  tools  │───▶│  hitl_node  │  │
│  └─────────┘    └─────────┘    └─────────┘    └─────────────┘  │
│                      │                              │           │
│                      ▼                              ▼           │
│                 ┌─────────┐                   ┌─────────┐       │
│                 │   END   │◀──────────────────│  store  │       │
│                 └─────────┘                   └─────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. Graph Structure (`app/graph/`)

| File | Purpose |
|------|---------|
| `builder.py` | Graph construction, checkpointing, store initialization |
| `nodes.py` | Agent node, HITL node with interrupt/approval logic |
| `routing.py` | Conditional edges (route to tools, HITL, or END) |
| `state.py` | AgentState definition (messages, user_id, metadata) |
| `tool_router.py` | Two-phase tool routing for model limits |

### 2. Tools (`app/tools/`)

| Tool | Purpose | HITL? |
|------|---------|-------|
| `update_tasks` | Create/update tasks | ✅ Yes |
| `update_profile` | Update user profile | ❌ No |
| `update_instructions` | Update planning preferences | ❌ No |
| `get_tasks` | List tasks | ❌ No |
| `mark_task_done` | Complete task | ❌ No |
| `web_search` | Web search | ❌ No |
| `dingtalk_*` | DingTalk MCP tools | ❌ No |

### 3. Human-in-the-Loop (HITL)

**Flow:**
```
1. LLM calls update_tasks → generates proposed tasks
2. Tool returns proposals (no store write)
3. hitl_node reads proposals → interrupt()
4. Frontend shows UI for approve/edit/reject
5. User submits → resume() with approval data
6. hitl_node applies changes to store
```

**Key Design:**
- Only `update_tasks` uses HITL (creates/modifies user data)
- Read-only tools (`get_tasks`, `search`) skip HITL
- Proposals are checkpointed, survive resume

### 4. Tool Routing (`app/graph/tool_router.py`)

**Problem:** GLM-5.1 has ~55 tool limit, but app has 106 tools (8 core + 98 DingTalk)

**Solution:** Two-phase routing
```
User Message → [Tool Router] → Select relevant tools (≤50) → [Agent] → Execute
```

- If tools ≤ `GLM_MAX_TOOLS`: Skip routing, use all tools
- Otherwise: LLM selects relevant tools based on message content
- Core tools always included

### 5. Configuration

**Environment Variables (`.env`):**
```env
# LLM Provider
GLM_API_KEY=your_key
GLM_MODEL=glm-5.1
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
GLM_MAX_TOOLS=50  # Tool routing threshold

# Database
DATABASE_URL=postgresql://user:pass@localhost/db

# DingTalk MCP
DINGTALK_MCP_ENABLED=true
DINGTALK_APP_KEY=your_key
DINGTALK_APP_SECRET=your_secret
```

## Development Guidelines

### Adding a New Tool

1. Define tool function in `app/tools/<category>.py`
2. Add to `ALL_TOOLS` in `app/tools/__init__.py`
3. If it modifies user data, consider HITL workflow

### Adding HITL to a Tool

1. Tool should return proposals (not write to store)
2. Add parsing logic in `_parse_task_proposals()` in `nodes.py`
3. Add summary builder in `build_hitl_summary()` in `debug_utils.py`

### Debug Printing

Use functions from `app/core/debug_utils.py`:
```python
from app.core.debug_utils import print_proposed_tasks, print_approval_result

print_proposed_tasks(proposed)
print_approval_result(approval, rejected_keys, edited_tasks)
```

## Common Issues

### 1. GLM-5.1 "API 调用参数有误" (Error 1210)
**Cause:** Too many tools (>55) sent to model
**Fix:** Enable tool routing (`GLM_MAX_TOOLS=50`)

### 2. HITL Resume Key Mismatch
**Cause:** Node restarts on resume, extractor regenerates keys
**Fix:** Use deterministic keys (content hash) in `update_tasks`

### 3. Tool Not Available
**Cause:** Tool not in `ALL_TOOLS` or filtered by router
**Fix:** Check `app/tools/__init__.py` and tool router logs

## Testing

```bash
# Run specific test
python -m pytest tests/test_graph.py -v

# Test GLM API directly
python test_glm_api.py

# Test with all tools
python test_glm_all_tools.py
```

## Project Structure

```
app/
├── agents/           # Agent configuration, LLM setup
├── chat/             # Chat API endpoints
├── core/             # Config, debug utils, exceptions
├── graph/            # LangGraph nodes, routing, state
├── schemas/          # Pydantic models
├── store/            # Memory store (profile, tasks)
├── tasks/            # Task API endpoints
├── tools/            # Agent tools (memory, search, dingtalk)
├── transcription/    # Audio/video transcription
└── main.py           # FastAPI app
```

## Key Dependencies

- **LangGraph**: Agent orchestration with checkpointing
- **LangChain**: LLM abstractions, tool definitions
- **TrustCall**: Structured extraction for task/profile updates
- **FastAPI**: HTTP API framework
- **PostgreSQL**: Persistent checkpoint and memory store