---
name: bugfix
description: 对 GitHub issue 执行完整的自动 bugfix 流程——读取 issue → 确保 master 拉到最新代码且工作区干净 → 基于最新 master 新建 bugfix 分支 → 修改代码 → 跑单元/集成测试 → 推送 → 创建 PR → 推送修复内容+PR 链接到 OpenClaw 微信 → 收尾切回 master 分支。适用于「自动修复 issue」「bugfix」「fix this issue」「修复这个 bug」等请求，调用方式 /bugfix <issue编号>。
---

# Bugfix 工作流（GitHub issue → 修复 → PR）

本 skill 把一条 GitHub issue 从「读需求」推进到「合入 PR」：读 issue、基于 master 建 bugfix 分支、改代码、跑测试、推送、开 PR，并在 PR 创建成功后把「修复内容 + PR 链接」推送到 OpenClaw 微信（已验证链路）。GitHub 操作一律走**用户级 GitHub MCP**（`mcp__github__*`），不依赖 `gh` CLI（本机未安装）。

**路径约定：** 以下命令均在仓库根目录 `F:\agents projects\AI_Note\backend`（即 `<unit>/`）执行。本 skill 目录为 `.claude/skills/bugfix/`。

## 前置条件

- 用户级 GitHub MCP 已配置（`claude mcp list` 中 `github` 为 Connected）。本仓库：`Jiangshan2128/langgraph-toolcall-mcp-backend`。
- `git` 与 `uv`（≥0.11）。`gh` **不需要**。
- 测试命令用 uv workspace：`uv run pytest tests/`（基线 169 passed）。

## 流程（9 步）

> **⚠️ 多 issue 串行约束（硬性）：** 若目标 repo 的 issue 列表里有多个待修复 issue，**严格按先后顺序一个一个处理，绝不并发/同时处理**。每次只处理一个，且必须完整走完第 1~9 步（读 issue → 修复 → 跑测试 → 推送 → 创建 PR → **微信通知成功** → **切回 master**）之后，才能开始下一个 issue。禁止：一次 checkout 多个分支、把多个修复混进同一个 commit/PR、或在前一个 PR 未创建成功时就跳到下一个。用 GitHub MCP 的 `mcp__github__list_issues` 获取 issue 列表，**必须带 `state="OPEN"` 只取 open issue**（已关闭/已合入的 issue 一律跳过、不在处理范围），按创建顺序从最旧（或列表顺序）开始逐个执行。

### 0. 确定 owner/repo 与 issue 编号
```bash
git remote get-url origin
```
从 URL 解析 owner/repo。issue 编号来自调用参数：`/bugfix <issue-number>`。

### 1. 查看 issue 具体内容
调用 `mcp__github__issue_read`，参数 `owner`/`repo` 取自第 0 步、`issue_number=<N>`、`method="get"`。
通读 title/body/comments，提取：复现步骤、期望行为、涉及模块；信息不足时再读 `method="get_comments"`。

### 2. 确保在 master 分支且工作区干净，并拉到最新代码
```bash
git fetch origin
git checkout master
git pull origin master    # 必须拉到最新 master，新分支要基于它创建
git status --porcelain    # 必须为空
```
不干净就**先处理无关改动**（`git stash push` 或提交），绝不带进 bugfix 分支。

### 3. 新建 bugfix 分支
**必须在第 2 步 master 已拉到最新代码之后**再新建分支，保证 bugfix 分支基线是最新 master：
```bash
git switch -c bugfix/<issue编号>-<短描述slug>
```
推荐直接用驱动脚本（内含第 2、3 步守卫，已验证）：
```bash
.claude/skills/bugfix/scripts/prepare-bugfix-branch.sh <issue编号> <slug>
```

### 4. 根据 issue 描述修改代码
- 改动最小化，只修 issue 涉及的行为；遵守 `AGENTS.md` 的 import 约定（`ainote.*` / `app.*`）。
- 自查：类型、边界条件、对现有调用方的影响。

### 5. 执行单元测试 + 集成测试
```bash
uv run pytest tests/              # 全量（当前基线 169 passed）
uv run pytest tests/test_xxx.py -v  # 单独跑相关文件
```
- 修复涉及新边界行为时，先在 `tests/` 补用例再改实现。
- 必要时写一次性验证脚本（如 `uv run python scripts/verify_fix.py`），验证完转为正式测试或删除。

### 6. 提交并推送至远端 bugfix 分支
```bash
git add <相关文件>                 # 只加相关文件，勿 git add -A
git commit -m "fix(<scope>): <一句话描述，关联 issue #N>"
git push -u origin <branch>
```
提交信息遵循仓库 Conventional Commits 风格，以 `fix(...)` 开头，并附 `Co-Authored-By: Claude <noreply@anthropic.com>`。

### 7. 创建 PR
先查是否有 PR 模板（当前**没有**，已验证）：`mcp__github__get_file_contents` 查 `.github/pull_request_template.md` 与根目录 `pull_request_template.md`。
然后调用 `mcp__github__create_pull_request`：
- `owner`/`repo` 同上，`head=<bugfix分支>`，`base=master`
- `title`：`fix: <描述>`
- `body`：改动概述、测试结果（如「`uv run pytest tests/` → 169 passed」），末尾写 `Closes #<issue>`

### 8. 通知 OpenClaw（推送微信）
创建 PR **成功后**（拿到 `html_url`），把「修复内容 + PR 链接」推送到 OpenClaw 微信：

1. **取 PR 链接**：`mcp__github__create_pull_request` 返回的 `html_url`。
2. **写 UTF-8 payload 文件**（用 Write 工具写 `.claude/tmp/openclaw-bugfix-notify.json`，Write 输出天然 UTF-8）。`message` 里**同时嵌入**修复内容和 PR 链接，措辞写成「请原样转发」以防 agent 改写：
```json
{
  "message": "请原样转发这条 bugfix 通知：\n【Banana Todo List bugfix 完成】#<issue>: <issue 标题>\n修复内容: <改动摘要>\nPR: <PR html_url>",
  "name": "BananaTodoList-bugfix",
  "channel": "openclaw-weixin",
  "to": "o9cq800xaFLWUCAxmzf_YhQ5uxsw@im.wechat",
  "deliver": true
}
```
3. **发送**（`OPENCLAW_HOOKS_URL` / `OPENCLAW_HOOKS_TOKEN` 定义在 `.claude/settings.local.json` 的 `env`；若 Bash 环境里取不到，用 `python -c` 解析该 JSON 取 token）：
```bash
curl -s --noproxy '*' --max-time 60 -X POST "${OPENCLAW_HOOKS_URL:-http://localhost:18789/hooks/agent}" \
  -H 'Content-Type: application/json; charset=utf-8' \
  -H "x-openclaw-token: ${OPENCLAW_HOOKS_TOKEN}" \
  --data-binary @.claude/tmp/openclaw-bugfix-notify.json
```
   - 返回 `{"ok":true,"runId":...}` 即推送成功；`{"ok":false,"error":"message required"}` 表示路由活着但缺 message。
   - ⚠️ 中文**必须**走 UTF-8 文件 + `--data-binary`；直接写 curl `-d` 会在 Windows 命令行乱码。
   - 推送成功后删除临时 payload 文件（`.claude/tmp/openclaw-bugfix-notify.json`）。

### 9. 收尾
1. **切回 master**：PR 创建且微信通知成功后，把工作区切回 master 分支，确保后续工作（下一个 issue / 日常开发）从干净基线开始，不留 bugfix 分支在检出状态：
   ```bash
   git checkout master
   git pull origin master
   ```
2. 回报：PR 链接、改动摘要、测试结果、微信通知结果（runId）。若 body 含 `Closes #N`，PR 合并后 issue 自动关闭。bugfix 流程结束。

## Gotchas（本环境实测）

- **`gh` 未安装** —— 别用 `gh pr create` / `gh issue view`，一律走 `mcp__github__*`。
- **`python -m pytest tests/` 直接跑不通** —— `ainote` 不在 sys.path 且缺依赖（`groq`、`pytest-asyncio` 等）；必须用 `uv run pytest tests/`（uv workspace 会同步 `packages/harness` 依赖并注入 `.venv`）。
- **AGENTS.md 里说的 `app/main.py` sys.path 注入已不存在** —— 文档过时，别依赖它，依赖 `uv run`。
- **工作区干净是硬性守卫** —— bugfix 分支必须从干净的 master 拉出；`git status --porcelain` 会列出未跟踪文件，`.venv`/`__pycache__` 等已被 gitignore 不受影响。
- **只 `git add` 相关文件** —— 本仓库活跃开发分支是 `develop`，bugfix 基于 `master`，不要把 develop 上的半成品夹带进来。
- **推送微信必须 UTF-8 文件 + `--data-binary`** —— 中文 payload 直接写在 curl `-d` 里会在微信里乱码（Windows 命令行非 UTF-8）；必须用 Write 写 UTF-8 文件再 `curl --data-binary @file` 发送。curl 还要加 `--noproxy '*'` 绕开本机 Clash 代理（`127.0.0.1:7897`）。
- **`OPENCLAW_HOOKS_TOKEN` 未注入时** —— 从 `.claude/settings.local.json` 的 `env.OPENCLAW_HOOKS_TOKEN` 取（`python -c "import json;print(json.load(open('.claude/settings.local.json',encoding='utf-8'))['env']['OPENCLAW_HOOKS_TOKEN'])"`），勿硬编码进脚本。

## Troubleshooting（本环境实际遇到）

| 症状 | 修复 |
|---|---|
| `python -m pytest` 收集报 `No module named 'ainote'` | 改用 `uv run pytest tests/` |
| `No module named 'groq'` / `Unknown pytest.mark.asyncio` | 走 `uv run`（自动同步依赖）；不要手动 pip 逐个装 |
| GitHub MCP 连接失败：`does not support dynamic client registration` | 该托管服务器不支持 OAuth DCR，需 PAT：`claude mcp add-json github '{"type":"http","url":"https://api.githubcopilot.com/mcp","headers":{"Authorization":"Bearer <PAT>"}}' --scope user` |
| `mcp__github__issue_read` 报不存在 | 用 `git remote get-url origin` 核实 owner/repo，确认 issue 编号 |
| 驱动脚本报「工作区不干净」 | 先 `git stash push` 无关改动，或确认没有遗留未跟踪文件 |
| 微信通知乱码 | payload 写成 UTF-8 文件再用 `--data-binary` 发送；不要在 curl `-d` 里直接写中文 |
| 微信通知返回 `message required` | hooks 路由活着但缺 message，确认 JSON 里有非空 `message` |
| curl 微信通知卡住/超时 | 加 `--noproxy '*'`，绕开本机 `127.0.0.1:7897` 的 Clash 代理 |
| hooks 返回 401/403 | `x-openclaw-token` 与 `.claude/settings.local.json` 的 `OPENCLAW_HOOKS_TOKEN`（即 openclaw.json `hooks.token`）不一致 |

## 驱动脚本

`scripts/prepare-bugfix-branch.sh <issue> [slug]` 封装第 2/3 步（确保 master + 干净 + 建/复用分支）。已在沙盒 git 仓库验证三条路径：脏工作区拒绝、干净建分支、分支复用。
