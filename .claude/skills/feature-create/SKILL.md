---
name: feature-create
description: 对 feature 需求执行完整的自动化开发流程——切换到 master 分支 → 在 master 上 pull 最新代码 → 基于最新 master 新建 feature 分支 → 实现功能（尊重现有架构/社区最佳实践/官方规范）→ 跑测试 → 测试成功后提交推送并创建 PR → 本地分支保持不动（便于继续测试）。适用于「新建功能」「feature」「实现这个功能」「加个新功能」等请求，调用方式 /feature-create <功能描述或slug>。
---

# Feature 开发工作流（需求 → 实现 → PR）

本 skill 把一条 feature 需求从「开工」推进到「合入 PR」：切到 master → pull 最新 → 基于最新 master 建 feature 分支 → 实现功能 → 跑测试 → 提交推送 → 创建 PR → 收尾保持本地分支不动（便于继续测试）。GitHub 操作一律走**用户级 GitHub MCP**（`mcp__github__*`），不依赖 `gh` CLI（本机未安装）。

**路径约定：** 以下命令均在仓库根目录 `F:\agents projects\AI_Note\backend`（即 `<unit>/`）执行。本 skill 目录为 `.claude/skills/feature-create/`。

## 前置条件

- 用户级 GitHub MCP 已配置（`claude mcp list` 中 `github` 为 Connected）。本仓库：`Jiangshan2128/langgraph-toolcall-mcp-backend`。
- `git` 与 `uv`（≥0.11）。`gh` **不需要**。
- 测试命令用 uv workspace：`uv run pytest tests/`（基线 169 passed）。

## 流程（7 步）

### 1. 切换到 master 分支
```bash
git checkout master
```
若当前分支有未提交改动，先处理（`git stash push` 或提交），不带进 feature 分支。

### 2. 在 master 分支上 pull 最新的代码
```bash
git fetch origin
git pull origin master
git status --porcelain   # 必须为空
```
必须拿到最新 master，新 feature 分支要基于它创建。

### 3. 基于最新的 master 创建新的 feature 分支
```bash
git switch -c feature/<短描述slug>
```
推荐直接用驱动脚本（内含第 1~3 步守卫，已验证）：
```bash
.claude/skills/feature-create/scripts/prepare-feature-branch.sh <slug>
```

### 4. 实现 feature 功能
- 改动最小化，只实现该 feature 涉及的行为；遵守 `AGENTS.md` 的 import 约定（`ainote.*` / `app.*`）与项目结构。
- **尊重现有代码架构**：复用既有中间件、工具、状态/图结构、配置层，不在同一层另起一套平行实现。
- **遵循社区最佳实践与官方规范**：LangGraph/LangChain 官方用法、PEP 8、仓库现有 Conventional Commits 风格。
- 自查：类型、边界条件、对现有调用方的影响。

### 5. 执行测试
```bash
uv run pytest tests/              # 全量（当前基线 169 passed）
uv run pytest tests/test_xxx.py -v  # 单独跑相关文件
```
- 新功能涉及新边界行为时，先在 `tests/` 补用例再改实现。

### 6. 测试成功后提交推送并创建 PR
```bash
git add <相关文件>                 # 只加相关文件，勿 git add -A
git commit -m "feat(<scope>): <一句话描述>"
git push -u origin <branch>
```
提交信息遵循仓库 Conventional Commits 风格，以 `feat(...)` 开头，并附 `Co-Authored-By: Claude <noreply@anthropic.com>`。
然后调用 `mcp__github__create_pull_request`：
- `owner`/`repo` 同上，`head=<feature分支>`，`base=master`
- `title`：`feat: <描述>`
- `body`：改动概述、测试结果（如「`uv run pytest tests/` → 169 passed」）；若由 GitHub issue 驱动，末尾写 `Closes #<issue>`

### 7. 保持分支不动（便于继续测试）
PR 创建成功后，**本地分支保持在当前 feature 分支上，不要切走** —— 提交后用户还需要在分支上继续测试/调整。若有进一步改动，直接在本分支提交并推送（PR 自动更新）。下一次启动 feature 流程时，第 1 步会自动 `git checkout master` 切走。

## Gotchas（本环境实测）

- **`gh` 未安装** —— 别用 `gh pr create`，一律走 `mcp__github__*`。
- **`python -m pytest tests/` 直接跑不通** —— `ainote` 不在 sys.path 且缺依赖（`groq`、`pytest-asyncio` 等）；必须用 `uv run pytest tests/`（uv workspace 会同步 `packages/harness` 依赖并注入 `.venv`）。
- **只 `git add` 相关文件** —— 本仓库活跃开发分支是 `develop`，feature 基于 `master`，不要把 develop 上的半成品夹带进来。
- **工作区干净是硬性守卫** —— feature 分支必须从干净的 master 拉出；`git status --porcelain` 会列出未跟踪文件，`.venv`/`__pycache__` 等已被 gitignore 不受影响。
- **GitHub MCP 连接失败** —— 该托管服务器不支持 OAuth DCR，需 PAT：`claude mcp add-json github '{"type":"http","url":"https://api.githubcopilot.com/mcp","headers":{"Authorization":"Bearer <PAT>"}}' --scope user`。

## Troubleshooting（本环境实际遇到）

| 症状 | 修复 |
|---|---|
| `python -m pytest` 收集报 `No module named 'ainote'` | 改用 `uv run pytest tests/` |
| `No module named 'groq'` / `Unknown pytest.mark.asyncio` | 走 `uv run`（自动同步依赖）；不要手动 pip 逐个装 |
| GitHub MCP 连接失败：`does not support dynamic client registration` | 该托管服务器不支持 OAuth DCR，需 PAT，见 Gotchas |
| `mcp__github__create_pull_request` 报不存在 | 用 `git remote get-url origin` 核实 owner/repo |
| 驱动脚本报「工作区不干净」 | 先 `git stash push` 无关改动，或确认没有遗留未跟踪文件 |

## 驱动脚本

`scripts/prepare-feature-branch.sh <slug>` 封装第 1~3 步（确保 master 最新 + 干净 + 建/复用分支）。
