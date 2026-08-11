#!/usr/bin/env python3
"""验证 ChatDeepSeek 是否仍需禁用 thinking mode。

背景：TrustCall 内部会发 tool_choice="required"（enable_inserts=True → "any"
→ "required"）。DeepSeek reasoning 模型的 thinking mode 服务端只支持
tool_choice="auto"/"none"，遇 "required" 返回 400。

本脚本用替换后的 ChatDeepSeek 类直接对 DeepSeek API 发真实请求，对比：
  1. ChatDeepSeek + thinking 开启（不 disable）+ tool_choice="required" → 期望 400
  2. ChatDeepSeek + thinking 关闭（disable）+ tool_choice="required" → 期望 200

如果 1 返回 400，证明 ChatDeepSeek 不能免除 disable thinking（服务端限制）。
如果 1 返回 200，证明新版模型/类已支持——那 config.yaml 的 thinking: false
就可以改成 true。

运行（需 .env 里的 DEEPSEEK_API_KEY）：
    python scripts/verify_chatdeepseek_thinking.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "harness"))

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_deepseek import ChatDeepSeek

load_dotenv()

MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"

# 模拟 TrustCall 的 tool_choice="required" 场景（enable_inserts=True 的行为）
TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "test_tool",
        "description": "A test tool.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def run_case(label: str, *, thinking_disabled: bool) -> None:
    """用 ChatDeepSeek 发一次 tool_choice=required 的请求，打印结果。"""
    kwargs: dict = {
        "model": MODEL,
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "api_base": BASE_URL,
        "temperature": 0,
    }
    if thinking_disabled:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    model = ChatDeepSeek(**kwargs)
    bound = model.bind_tools([TOOL_SCHEMA], tool_choice="required")

    print(f"──── {label} ────")
    try:
        resp = bound.invoke([HumanMessage(content="call the tool")])
        tool_calls = resp.tool_calls
        print(f"  ✅ 200 — 成功，tool_calls={len(tool_calls)}")
        for tc in tool_calls:
            print(f"     tool: {tc['name']}, args: {tc['args']}")
    except Exception as e:
        err = str(e)
        # 提取 HTTP 状态码
        status = "?"
        for marker in ("Status: ", "status_code=", "code="):
            if marker in err:
                import re
                mm = re.search(rf"{marker}(\d{{3}})", err)
                if mm:
                    status = mm.group(1)
                    break
        print(f"  ❌ {status} — {err[:200]}")
    print()


def main() -> None:
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("错误: DEEPSEEK_API_KEY 未设置（.env）")
        sys.exit(1)
    print(f"模型: {MODEL} | 端点: {BASE_URL}")
    print(f"场景: 模拟 TrustCall 的 tool_choice=\"required\"\n")
    run_case("ChatDeepSeek + thinking 开启 + tool_choice=required", thinking_disabled=False)
    run_case("ChatDeepSeek + thinking 关闭 + tool_choice=required", thinking_disabled=True)


if __name__ == "__main__":
    main()
