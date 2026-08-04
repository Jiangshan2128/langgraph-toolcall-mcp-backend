#!/usr/bin/env python3
"""分析 ChatOpenAI 与 ChatDeepSeek 的请求 payload 差异（差异 1：消息格式修正）。

背景：ChatDeepSeek 是 ChatOpenAI 的子类，但 override 了 ``_get_request_payload``，
会在发送前修正 DeepSeek API 不接受的两种消息格式：
  - tool 消息：content 为列表时 → json.dumps 成字符串
  - assistant 消息：content 为列表时 → 只提取 text 块拼接成字符串

本脚本用三种场景实测两者最终发出去的 payload，验证在什么条件下差异会触发、
当前项目（assistant 消息 content 为 None/纯字符串、tool 消息为 json 字符串、
非 thinking 模型）为何不会踩到该差异。

运行：
    python scripts/analyze_payload_diff.py
"""

import os
import sys
from pathlib import Path

# 使 `from ainote...` 可导入（与 app/main.py 一致的 sys.path 处理）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "harness"))

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

# 不实际发请求，只测 payload 构造；用 fake key 即可
MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"

model_openai = ChatOpenAI(model=MODEL, api_key="fake", base_url=BASE_URL)
model_ds = ChatDeepSeek(model=MODEL, api_key="fake", base_url=BASE_URL)


def show_assistant_and_tool(payload: dict, label: str) -> None:
    """打印 payload 中 assistant 与 tool 消息的 content 形态。"""
    for m in payload["messages"]:
        if m["role"] in ("assistant", "tool"):
            print(f"  {label} [{m['role']}] content:", repr(m["content"])[:100])


def scenario_a() -> None:
    """assistant 带 tool_calls（TrustCall 多轮对话常见）→ 两者应无差异。"""
    print("=" * 70)
    print("场景 A：assistant 带 tool_calls（content 为 None）")
    print("=" * 70)
    msgs = [
        SystemMessage(content="你是助手"),
        HumanMessage(content="帮我安排任务"),
        AIMessage(
            content="",
            tool_calls=[ToolCall(id="c1", name="update_tasks", args={"title": "x"})],
        ),
        ToolMessage(content='{"type":"task_proposals",...}', tool_call_id="c1"),
        AIMessage(content="好的，已安排"),
    ]
    show_assistant_and_tool(model_openai._get_request_payload(msgs), "ChatOpenAI")
    show_assistant_and_tool(model_ds._get_request_payload(msgs), "ChatDeepSeek")


def scenario_b() -> None:
    """assistant content 是列表（带 text 块 + tool_calls）→ 差异 1 触发点。"""
    print("=" * 70)
    print("场景 B：assistant content 为列表（差异 1 触发点）")
    print("=" * 70)
    ai_list = AIMessage(
        content=[{"type": "text", "text": "我先查一下"}, {"type": "text", "text": "再确认"}],
        tool_calls=[ToolCall(id="c2", name="get_tasks", args={})],
    )
    msgs = [HumanMessage(content="hi"), ai_list]
    show_assistant_and_tool(model_openai._get_request_payload(msgs), "ChatOpenAI")
    show_assistant_and_tool(model_ds._get_request_payload(msgs), "ChatDeepSeek")


def scenario_c() -> None:
    """tool 消息 content 是列表 → 差异 1 的另一半。"""
    print("=" * 70)
    print("场景 C：tool 消息 content 为列表（差异 1 另一触发点）")
    print("=" * 70)
    tool_msg_list = ToolMessage(
        content=[{"type": "text", "text": "结果1"}, {"type": "text", "text": "结果2"}],
        tool_call_id="c1",
    )
    msgs = [
        HumanMessage(content="hi"),
        AIMessage(content="", tool_calls=[ToolCall(id="c1", name="update_tasks", args={})]),
        tool_msg_list,
    ]
    show_assistant_and_tool(model_openai._get_request_payload(msgs), "ChatOpenAI")
    show_assistant_and_tool(model_ds._get_request_payload(msgs), "ChatDeepSeek")


def why_project_safe() -> None:
    """解释当前项目为何不会踩到差异 1。"""
    print("=" * 70)
    print("为什么当前项目不会遇到差异 1 的错误")
    print("=" * 70)
    reasons = [
        "1. 所有 AIMessage 都是纯字符串 content（'No task changes...' 等），带 tool_calls 的 content 为 None——没有列表",
        "2. update_tasks 返回 json.dumps 的字符串，ToolNode 构造的 ToolMessage.content 是 str 而非 list",
        "3. 模型是 deepseek-v4-flash（非 thinking 的 chat 模型），已用 extra_body 禁用 thinking，不产生列表 content",
    ]
    for r in reasons:
        print("  " + r)
    print()
    print("结论：三种条件都让 content 保持简单字符串，ChatOpenAI 原样发送也不触发 DeepSeek 的格式限制。")


if __name__ == "__main__":
    scenario_a()
    print()
    scenario_b()
    print()
    scenario_c()
    print()
    why_project_safe()
