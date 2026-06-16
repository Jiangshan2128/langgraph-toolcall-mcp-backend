# AI Note 后端接口协议

> Base URL: `/api/v1`

---

## 1. 普通聊天

### `POST /chat`

同步请求，等待完整回复后返回。

**Request Body:**

```json
{
  "message": "帮我拆解这个项目的任务",
  "user_id": "default"
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `message` | string | ✅ | — | 用户消息，最小长度 1 |
| `user_id` | string | ❌ | `"default"` | 用户标识，用于隔离记忆 |

**Response (200):**

```json
{
  "answer": "好的，我帮你把这个项目拆解成以下任务...",
  "tasks": [
    {
      "key": "abc123",
      "title": "需求分析",
      "description": "整理产品需求文档",
      "assignee": null,
      "priority": "P1",
      "time": "",
      "deadline": null,
      "pre_task": null,
      "status": "not started"
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `answer` | string | AI 自然语言回复 |
| `tasks` | Task[] | 当前用户的完整任务列表 |

---

## 2. 流式聊天（SSE）

### `POST /chat/stream`

流式请求，通过 Server-Sent Events 逐步推送数据。

**Request Body:** 同上

**Response:** `Content-Type: text/event-stream`

### SSE 事件流

前端收到的完整事件流示例：

```
event: connected
data: 

event: message
data: 好的

event: message
data: ，

event: message
data: 我来帮你

event: message
data: 拆解任务

event: tasks
data: [{"key":"abc123","title":"需求分析","description":"整理产品需求文档","assignee":null,"priority":"P1","time":"","deadline":null,"pre_task":null,"status":"not started"}]

event: done
data: 
```

### 事件类型

| event | data 类型 | 说明 | 前端处理 |
|---|---|---|---|
| `connected` | `""` | 连接建立确认 | 可忽略或显示"连接中..." |
| `message` | string | AI 回复的一个 token 片段 | 追加到聊天消息末尾 |
| `tasks` | JSON string → Task[] | 完整任务列表（流结束时推送） | **替换**当前任务列表 |
| `error` | string | 错误信息 | 显示错误提示 |
| `done` | `""` | 流结束标志 | 关闭连接，隐藏 loading |

### 前端接入示例

```javascript
async function streamChat(message, userId = "default") {
  const response = await fetch("/api/v1/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, user_id: userId }),
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  let currentEvent = "";
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // 按行解析 SSE
    const lines = buffer.split("\n");
    buffer = lines.pop() || ""; // 最后一行可能不完整，保留

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        const data = line.slice(6);

        switch (currentEvent) {
          case "connected":
            console.log("SSE connected");
            break;

          case "message":
            // 追加文本到聊天界面
            appendToChatMessage(data);
            break;

          case "tasks":
            // 替换任务列表（data 是 JSON 字符串）
            const tasks = JSON.parse(data);
            replaceTaskList(tasks);
            break;

          case "error":
            showError(data);
            break;

          case "done":
            console.log("Stream finished");
            break;
        }
      }
    }
  }
}
```

---

## 3. 数据模型

### Task

```typescript
interface Task {
  key: string;               // Store 唯一标识，用于 delete/update 接口
  title: string;          // 任务标题
  description: string | null;  // 任务详情
  assignee: string | null;     // 负责人
  priority: "P0" | "P1" | "P2"; // 优先级：P0=紧急 P1=重要 P2=日常
  time: string;                // 何时开始，如 "today"、"next week"
  deadline: string | null;     // 截止日期 (YYYY-MM-DD 或描述文本)
  pre_task: string | null;     // 前置任务标题
  status: "not started" | "in progress" | "done" | "archived"; // 状态
}
```

### ChatRequest

```typescript
interface ChatRequest {
  message: string;    // 必填，用户消息
  user_id?: string;   // 可选，默认 "default"
}
```

### ChatResponse

```typescript
interface ChatResponse {
  answer: string;     // AI 回复文本
  tasks: Task[];      // 任务列表
}
```

### DeleteTaskRequest

```typescript
interface DeleteTaskRequest {
  key: string;        // 必填，要删除的 task 的 store key
  user_id?: string;   // 可选，默认 "default"
}
```

### UpdateTaskRequest

```typescript
interface UpdateTaskRequest {
  key: string;        // 必填，要更新的 task 的 store key
  user_id?: string;   // 可选，默认 "default"
  updates: Partial<Task>;  // 必填，要更新的字段
}
```

---

## 4. 删除任务

### `DELETE /chat/task`

删除指定 key 的 task。

**Request Body:**

```json
{
  "key": "abc123",
  "user_id": "default"
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `key` | string | ✅ | — | 要删除的 task 在 store 中的 key |
| `user_id` | string | ❌ | `"default"` | 用户标识 |

**Response (200):**

```json
{
  "ok": true,
  "message": "Task 'abc123' deleted"
}
```

**Error (404):** task 不存在

```json
{
  "detail": "Task with key 'abc123' not found"
}
```

### 前端示例

```javascript
// ---------- 删除任务 ----------
async function deleteTask(key, userId = "default") {
  const response = await fetch("/api/v1/chat/task", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, user_id: userId }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "删除失败");
  }

  return response.json();
}

// ---------- 更新任务 ----------
async function updateTask(key, updates, userId = "default") {
  const response = await fetch("/api/v1/chat/task", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, user_id: userId, updates }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "更新失败");
  }

  return response.json();
}

// ---------- 在 React 组件中使用 ----------
function TaskCard({ task }) {
  // task.key 就是从后端返回的 store key

  const handleDelete = async () => {
    await deleteTask(task.key);
    removeTaskFromList(task.key);       // 从列表中移除
  };

  const handleToggleDone = async () => {
    const result = await updateTask(task.key, {
      status: task.status === "done" ? "not started" : "done",
    });
    replaceTaskInList(task.key, result.task); // 替换为更新后的 task
  };

  const handleChangePriority = async (newPriority) => {
    const result = await updateTask(task.key, { priority: newPriority });
    replaceTaskInList(task.key, result.task);
  };

  return (
    <div>
      <h3>{task.title}</h3>
      <span>{task.status}</span>
      <button onClick={handleToggleDone}>
        {task.status === "done" ? "重新开始" : "标记完成"}
      </button>
      <button onClick={handleDelete}>删除</button>
    </div>
  );
}
```

---

## 5. 更新任务

### `PATCH /chat/task`

更新指定 task 的部分字段（增量合并，不传的字段保持不变）。

**Request Body:**

```json
{
  "key": "abc123",
  "user_id": "default",
  "updates": {
    "status": "done",
    "priority": "P0"
  }
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `key` | string | ✅ | — | 要更新的 task 在 store 中的 key |
| `user_id` | string | ❌ | `"default"` | 用户标识 |
| `updates` | object | ✅ | — | 要更新的字段键值对，如 `{"status": "done"}` |

**Response (200):**

```json
{
  "ok": true,
  "task": {
    "key": "abc123",
    "title": "需求分析",
    "description": "整理产品需求文档",
    "assignee": null,
    "priority": "P0",
    "time": "",
    "deadline": null,
    "pre_task": null,
    "status": "done"
  }
}
```

**Error (404):** task 不存在

```json
{
  "detail": "Task with key 'abc123' not found"
}
```

---

## 6. 错误处理

| HTTP 状态码 | 场景 |
|---|---|
| 422 | 请求参数校验失败（如 `message` 为空） |
| 500 | 服务端内部错误 |

SSE 流内错误通过 `event: error` 推送，data 为错误描述字符串。

---

## 7. 其他接口

### `GET /`

健康检查，返回服务状态。

```json
{
  "message": "Hi AI Note Backend is running",
  "version": "0.1.0"
}
```
