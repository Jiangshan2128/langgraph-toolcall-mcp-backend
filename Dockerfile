# AI Note Backend — Dockerfile for WeChat CloudBase (微信云托管)
#
# 运行形态（容器内必须满足）：
#   - FastAPI (uvicorn) 监听 80 端口（CloudBase 强制 80）
#   - Node.js  → DingTalk MCP 子进程（mcp_servers.json: npx -y dingtalk-mcp@latest）
#   - ffmpeg   → Groq 转写超长音频时切分（packages/harness/ainote/transcription/_ffmpeg.py）
#   - Python 3.13 + uv workspace 安装全部依赖
#
# 构建：  docker build -t ainote-backend .
# 运行：  docker run --rm -p 8000:80 -e GLM_API_KEY=... -e SUPABASE_URL=... ainote-backend

# ── Builder stage: install Python deps via uv ──────────────────────────────
FROM ghcr.io/astral-sh/uv:0.5-python3.13 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 先拷贝依赖清单（利用 uv 构建缓存，代码改动不重装依赖）
COPY pyproject.toml uv.lock ./
COPY packages/harness/pyproject.toml ./packages/harness/

# 安装全部依赖（含 workspace member ainote-harness）
RUN uv sync --frozen --no-dev --no-install-project

# 再把项目源码拷进来（覆盖 --no-install-project 未安装的部分）
COPY . .

RUN uv sync --frozen --no-dev

# ── Runtime stage ──────────────────────────────────────────────────────────
FROM python:3.13-slim

# 时区：CloudBase 容器默认 UTC，业务想用 Asia/Shanghai 的话解开注释
# ENV TZ=Asia/Shanghai

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # 容器/生产跳过 graph.png 生成，缩短冷启动（builder.py 读取此开关）
    SKIP_GRAPH_PNG=1 \
    # CloudBase 强制监听 80
    PORT=80 \
    PATH="/app/.venv/bin:$PATH"

# Node.js（DingTalk MCP 子进程）+ ffmpeg（Groq 转写切分）
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 只拷贝运行时需要的文件（保留源码 + .venv）
COPY --from=builder /app /app

EXPOSE 80

# 启动：uvicorn 监听 0.0.0.0:80（CloudBase 只认 80）
CMD ["uvicorn", "app.main:fastApi", "--host", "0.0.0.0", "--port", "80", "--workers", "1"]
