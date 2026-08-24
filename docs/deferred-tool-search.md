# Deferred MCP Tool Search — 原理与实现

## 问题

项目中集成了 DingTalk MCP（~100 个工具），加上核心工具总共 106+。GLM 模型限制最多 ~55 个工具，直接 bind 会报错。

之前用"两阶段路由"解决：先用轻量 LLM 从 106 个工具中筛选相关的，再把选中的 bind 给主 LLM。缺点是多了一次 LLM 调用，费 token 费时间。

## 方案：Deferred Tool Search

**MCP 工具不 bind 给 LLM**，而是让 LLM 通过一个 `tool_search` 工具按需发现。

### 核心思想

```
LLM 始终只 bind: 9 个核心工具 + 1 个 tool_search
MCP 工具: 不 bind → LLM 在 prompt 中看到名字 → 需要时调用 tool_search 搜索 → 激活后可用
```

### 优势

| 对比 | 两阶段路由 | Deferred Search |
|------|-----------|-----------------|
| LLM 调用次数 | 每次对话多 1 次（筛选） | 零额外调用 |
| Token 消耗 | 每次多 ~500 tokens | LLM 需要时才搜索 |
| LLM 工具列表 | ~55 个，仍然很大 | < 10 个 |
| 灵活性 | 被动筛选（系统决定） | 主动搜索（LLM 决定） |

---

## 完整流程

### 启动阶段

```
app 启动
  │
  ├─ build_graph()                      [builder.py]
  │   └─ 此时 ALL_TOOLS 只有核心工具，无 MCP
  │
  └─ init_graph()                       [builder.py]
      ├─ load_dingtalk_tools()          [dingtalk.py]
      │   ├─ MultiServerMCPClient → 获取 ~100 个 MCP 工具
      │   └─ register_mcp_tools(tools)  → 工具名记录到 MCP_TOOL_NAMES
      ├─ ALL_TOOLS.append(t)            [tools/__init__.py]
      │   └─ MCP 工具加入全局列表
      ├─ build_deferred_tool_setup()    [tool_search.py]
      │   ├─ is_mcp_tool() 筛选 MCP 工具
      │   ├─ DeferredToolCatalog → 可搜索目录
      │   └─ build_tool_search_tool(catalog) → 动态创建 tool_search（闭包持有 catalog）
      ├─ ALL_TOOLS.append(tool_search_tool)  ← 关键：tool_search 加入 ALL_TOOLS
      ├─ refresh_deferred_setup(setup)  [nodes.py]
      │   └─ 缓存 DeferredToolSetup
      └─ build_graph()                  [builder.py]
          └─ ToolNode(ALL_TOOLS) 包含 tool_search ✅
```

### 对话阶段

```
每次 agent_node 调用
  │
  ├─ get_deferred_setup_cached()        [nodes.py]
  │   └─ 取缓存的 DeferredToolSetup（获取 deferred_names）
  │
  ├─ get_deferred_tools_prompt_section() [tool_search.py]
  │   └─ 生成 <available-deferred-tools> 段落
  │       mcp_dingtalk-honor_createOrgHonor
  │       createTask
  │       createEvent
  │       ...
  │
  ├─ get_model_with_tools()             [tool_router.py]
  │   ├─ bind 核心工具 (update_tasks, get_tasks, web_search...)
  │   ├─ bind tool_search（在 ALL_TOOLS 中，ToolNode 能识别）
  │   └─ bind promoted_tools（之前搜索激活的工具）
  │
  └─ LLM 收到 prompt，看到工具名但 schema 未加载
```

### LLM 发现并使用 MCP 工具

```
LLM: "帮我在钉钉上创建一个任务"
     ↓ 看到 <available-deferred-tools> 中有 createTask
     ↓ 调用 tool_search("create task")
     ↓
tool_search(query="create task")        [tool_search.py]
  │  （catalog 通过闭包持有，直接搜索）
  │
  ├─ DeferredToolCatalog.search("create task")
  │   └─ 分词 ["create", "task"]，按匹配分数排序
  │   └─ 返回匹配的工具列表 [createTask, createEvent, ...]
  │
  ├─ 返回 ToolMessage（包含工具的完整 JSON schema）
  │
  └─ Command(update={"promoted_tools": ["createTask", ...]})
      └─ state["promoted_tools"] 被更新（累加器 _reduce_promoted）
      ↓
      图走到 agent（promoted_tools 已更新）
      ↓
      get_model_with_tools() 现在 bind 了 createTask
      ↓
      LLM 可以调用 createTask 了
```

---

## 文件结构

```
app/tools/tool_search.py          ← 核心：MCP_TOOL_NAMES、DeferredToolCatalog、
                                     build_tool_search_tool、DeferredToolSetup、
                                     get_deferred_tools_prompt_section
app/tools/__init__.py             ← ALL_TOOLS（初始只有核心工具，无 tool_search）
app/tools/dingtalk.py             ← load_dingtalk_tools 中调用 register_mcp_tools()
app/graph/state.py                ← promoted_tools 字段 + 累加器 reducer
app/graph/tool_router.py          ← get_model_with_tools（核心 + tool_search + promoted）
app/graph/nodes.py                ← _DEFERRED_SETUP 缓存、refresh_deferred_setup()
app/graph/builder.py              ← init_graph 中构建 setup、追加 tool_search、重建 graph
app/graph/routing.py              ← route_after_tools（取 state["messages"][-1]）
app/agents/config.py              ← prompt 中的 {deferred_tools} 占位符
```

---

## 关键函数说明

### 1. `register_mcp_tools(tools)` / `is_mcp_tool(t)`

```python
MCP_TOOL_NAMES: set[str] = set()

def register_mcp_tools(tools: list[BaseTool]) -> None:
    """记录 MCP 工具名到 MCP_TOOL_NAMES set。"""
    for t in tools:
        MCP_TOOL_NAMES.add(t.name)

def is_mcp_tool(t: BaseTool) -> bool:
    """通过名字判断是否是 MCP 工具。"""
    return t.name in MCP_TOOL_NAMES
```

**为什么不用 metadata 标记**：`langchain-mcp-adapters` 返回的 `StructuredTool` 的 metadata 属性不可靠。改用独立的 set 记录工具名。

**调用时机**：`load_dingtalk_tools()` 中，获取工具后立即注册。

### 2. `DeferredToolCatalog`

```python
@dataclass(frozen=True)
class DeferredToolCatalog:
    """所有 MCP 工具的可搜索目录。不可变，纯查询。"""
    tools: tuple[BaseTool, ...]

    @cached_property
    def names(self) -> frozenset[str]:
        """所有 MCP 工具名，用于生成 prompt。"""
        return frozenset(t.name for t in self.tools)

    def search(self, query: str) -> list[BaseTool]:
        """支持三种搜索语法：
        - "create task" → 分词 ["create", "task"]，按匹配分数排序
        - "select:createTask,createEvent" → 精确选择
        - "+dingtalk cal" → 名称中必须含 "dingtalk"，再按 "cal" 排序
        """
```

**搜索算法**：默认搜索把查询拆成 token，每个工具计算匹配分数：
- name 匹配一个 token = +2 分
- description 匹配一个 token = +1 分
- 按总分降序排列，返回 Top 5

这样 "dingtalk create task" 能匹配到 `createTask`（name 匹配 "create"+"task" = 4 分），即使没有 "dingtalk" 前缀。

### 3. `build_tool_search_tool(catalog)`

```python
def build_tool_search_tool(catalog: DeferredToolCatalog) -> BaseTool:
    @tool
    def tool_search(query: str, tool_call_id: ...) -> Command:
        matched = catalog.search(query)
        return Command(
            update={"promoted_tools": [t.name for t in matched]},
            messages=[ToolMessage(content=schemas_json, ...)],
        )
    return tool_search
```

**关键点**：`catalog` 通过**闭包**（closure）传给 `tool_search`。`tool_search` 是动态创建的，创建时 catalog 已被确定。

**为什么返回 `Command`**：LangGraph 的 `ToolNode` 原生支持 `Command`——`Command(update=...)` 可以直接更新 graph state（`promoted_tools`），不需要 tool 感知 store。

### 4. `build_deferred_tool_setup(all_tools)`

```python
def build_deferred_tool_setup(all_tools, *, enabled=True) -> DeferredToolSetup:
    deferred = [t for t in all_tools if is_mcp_tool(t)]
    if not deferred:
        return DeferredToolSetup(None, frozenset(), None)
    catalog = DeferredToolCatalog(tuple(deferred))
    return DeferredToolSetup(
        tool_search_tool=build_tool_search_tool(catalog),
        deferred_names=catalog.names,
        catalog_hash=catalog.hash,
    )
```

**作用**：从 `ALL_TOOLS` 中筛选 MCP 工具，创建 catalog 和 `tool_search`，打包成 `DeferredToolSetup`。

**调用时机**：`init_graph()` 中，DingTalk 工具加载后。

### 5. `DeferredToolSetup`

```python
@dataclass(frozen=True)
class DeferredToolSetup:
    tool_search_tool: BaseTool | None   # 动态创建的 tool_search（要追加到 ALL_TOOLS）
    deferred_names: frozenset[str]      # 显示在 prompt 中的 MCP 工具名列表
    catalog_hash: str | None            # 目录哈希（用于版本校验）
```

**`tool_search_tool` 的处理**：在 `init_graph()` 中，`tool_search_tool` 被追加到 `ALL_TOOLS`，这样 `ToolNode(ALL_TOOLS)` 才能识别并执行它。

### 6. `get_deferred_tools_prompt_section(names)`

```python
def get_deferred_tools_prompt_section(deferred_names: frozenset[str]) -> str:
    """生成系统 prompt 中的 <available-deferred-tools> 段落。"""
```

**为什么是单独的函数**：prompt 措辞可能需要反复调整，独立出来方便修改。

### 7. `get_model_with_tools()` — 工具绑定

```python
def get_model_with_tools(*, promoted_names=None):
    # 1. 核心工具始终 bind
    tools_to_bind = [core_tools...]

    # 2. tool_search 始终 bind（在 ALL_TOOLS 中）
    if "tool_search" in tool_map:
        tools_to_bind.append(tool_map["tool_search"])

    # 3. 之前搜索激活的工具也 bind
    for name in (promoted_names or []):
        if name not in core_tools and name != "tool_search":
            tools_to_bind.append(tool_map[name])

    return model.bind_tools(tools_to_bind)
```

**三级策略**：
- **核心工具**：始终可用（update_tasks, get_tasks, web_search...）
- **tool_search**：始终可用（LLM 通过它发现 MCP 工具）
- **已激活工具**：之前搜索过的 MCP 工具，持续可用

### 8. `_DEFERRED_SETUP` 缓存

```python
# nodes.py
_DEFERRED_SETUP = None

def get_deferred_setup_cached():
    return _DEFERRED_SETUP

def refresh_deferred_setup(setup):
    global _DEFERRED_SETUP
    _DEFERRED_SETUP = setup
```

**没有懒加载**：`_DEFERRED_SETUP` 只由 `init_graph()` 中的 `refresh_deferred_setup(setup)` 显式设置。避免了模块导入时序问题。

**使用方式**：`agent_node` 中调用 `get_deferred_setup_cached()` 读取 `deferred_names` 用于 prompt。

### 9. `_reduce_promoted` — state 累加器

```python
def _reduce_promoted(current: list[str] | None, update: list[str] | None) -> list[str]:
    """累加 promoted_tools，按 name 去重。"""
    if update is None:
        return current or []
    if current is None:
        return update
    merged = list(current)
    for name in update:
        if name not in merged:
            merged.append(name)
    return merged
```

**作用**：`state["promoted_tools"]` 是一个累加列表。每次 `tool_search` 返回新的工具名，都会追加而不是替换。这样 LLM 之前搜索激活的工具不会丢失。

### 10. `route_after_tools` — 工具节点后路由

```python
def route_after_tools(state) -> Literal["agent", "hitl_node"]:
    last = state["messages"][-1]
    if isinstance(last, ToolMessage) and last.name == "update_tasks":
        try:
            payload = json.loads(last.content)
            if payload.get("type") == "task_proposals":
                return "hitl_node"
        except ...
            pass
    return "agent"
```

**只检查最后一个消息**：直接取 `state["messages"][-1]`，不往前搜索。避免了历史消息中的旧 `update_tasks` 导致错误路由到 `hitl_node`。

---

## 完整的消息流

```
用户: "添加一个 learn llm 任务，并同步到钉钉"

agent → LLM(核心工具 + tool_search)
  │   prompt 中有 <available-deferred-tools>
  │
  ├→ LLM 调用 update_tasks → hitl_node(interrupt) → resume
  │
  ├→ LLM 看到 summary + 用户要求同步到钉钉
  │
  ├→ LLM 调用 tool_search("create task")
  │   └→ catalog.search("create task")
  │     分词 ["create", "task"]
  │     createTask:  name 匹配 "create"(+2) + "task"(+2) = 4分 ✅
  │     createEvent: name 匹配 "create"(+2) = 2分
  │
  tools → tool_search 执行
  │   ├→ 返回 ToolMessage(schema of createTask, createEvent, ...)
  │   └→ Command(update promoted_tools)
  │
  agent → LLM(核心工具 + tool_search + createTask + createEvent + ...)
  │   ├→ LLM 现在可以调用 createTask
  │   └→ LLM 调用 createTask(subject="learn llm", ...)
  │
  tools → createTask 执行
  │   └→ ToolMessage("待办已创建")
  │
  agent → LLM 回复用户
  │   └→ "已帮你添加任务并同步到钉钉"
```

---

## 关键设计决策

### 为什么 `tool_search` 是动态创建的？

因为 `tool_search` 需要通过闭包持有 `DeferredToolCatalog`（MCP 工具的可搜索目录）。catalog 在 DingTalk 工具加载后才能构建，所以 `tool_search` 必须在 `init_graph()` 中动态创建。

### 为什么 `tool_search` 要追加到 `ALL_TOOLS`？

因为 `ToolNode(ALL_TOOLS)` 只认识 `ALL_TOOLS` 里的工具。如果 `tool_search` 不在 `ALL_TOOLS` 中，LLM 调用它时 `ToolNode` 会报错 `"tool_search is not a valid tool"`。

在 `init_graph()` 中：
```python
setup = build_deferred_tool_setup(ALL_TOOLS)
ALL_TOOLS.append(setup.tool_search_tool)  # ← 追加到 ALL_TOOLS
refresh_deferred_setup(setup)
build_graph()  # → ToolNode(ALL_TOOLS) 包含 tool_search ✅
```

### 为什么用 `Command` 而不是 tool 直接写 store？

LangGraph 的 `ToolNode` 原生支持 `Command`——tool 返回 `Command(update=...)` 时，LangGraph 自动把 update 合并到 graph state。不需要 tool 感知 store 的存在。

### 为什么 `promoted_tools` 用累加器 reducer？

因为 LLM 可能多次调用 `tool_search` 发现不同的工具。如果用替换而不是累加，之前发现的工具会丢失。

### 为什么 `_DEFERRED_SETUP` 没有懒加载？

避免模块导入时序问题。`_DEFERRED_SETUP` 只在 `init_graph()` 中通过 `refresh_deferred_setup(setup)` 显式设置，确保 DingTalk 工具已加载完毕。

### 为什么 `route_after_tools` 只检查最后一个消息？

`state["messages"][-1]` 就是刚执行的 tool 的输出。如果往前搜，可能搜到历史消息中旧的 `update_tasks`，导致错误路由到 `hitl_node`。
