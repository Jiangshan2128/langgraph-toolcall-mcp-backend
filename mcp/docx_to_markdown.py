#!/usr/bin/env python3
"""
DOCX → Markdown 转换脚本

将 Word 文档 (.docx) 转换为 Markdown，保留标题层级、表格、列表等结构。

转换策略（按优先级）:
  1. mammoth → HTML (表格保留为 <table>) → markdownify (转 GFM pipe table)
  2. mammoth → Markdown (直接，但表格会丢失)  [回退]
  3. docx2txt (纯文本，无结构)                 [最后回退]

依赖安装:
    pip install mammoth markdownify

用法:
    python docx_to_markdown.py <input.docx>
    python docx_to_markdown.py <input.docx> -o output.md
    python docx_to_markdown.py <input.docx> --stdout      # 输出到终端预览
"""

import argparse
import sys
from pathlib import Path


def _docx_via_mammoth_html(docx_path: Path) -> str:
    """
    mammoth → HTML（保留表格）→ markdownify（转 GFM pipe table）。
    推荐路径，表格结构完好。
    """
    import mammoth
    import markdownify

    with open(docx_path, "rb") as f:
        result = mammoth.convert_to_html(
            f,
            # 可选: 自定义样式映射，将 Word 样式转为 Markdown 的 # 层级
            # style_map="p[style-name='Title'] => # h1:fresh"
        )
        if result.messages:
            for msg in result.messages:
                print(f"[mammoth] {msg}", file=sys.stderr)

    md = markdownify.markdownify(
        result.value,
        heading_style="ATX",       # 使用 # 风格标题
        bullets="-",               # 无序列表用 -
        strip=["hr", "img"],       # 删除无关元素
        autolinks=True,
    )
    return md


def _docx_via_mammoth_markdown(docx_path: Path) -> str:
    """mammoth 直接转 Markdown（表格会丢失）。"""
    import mammoth

    with open(docx_path, "rb") as f:
        result = mammoth.convert_to_markdown(f)
        if result.messages:
            for msg in result.messages:
                print(f"[mammoth] {msg}", file=sys.stderr)
    return result.value


def _docx_via_docx2txt(docx_path: Path) -> str:
    """docx2txt 纯文本提取（结构全丢，仅回退）。"""
    import docx2txt
    return docx2txt.process(str(docx_path))


def convert_to_markdown(docx_path: Path | str, prefer: str = "html") -> str:
    """
    将 DOCX 转为 Markdown。

    prefer 参数:
      "html"    (默认) mammoth→HTML→markdownify，表格保留最佳
      "direct"   mammoth 直接出 Markdown，表格会丢失
    """
    docx_path = Path(docx_path)

    # --- 路线A: mammoth → HTML → markdownify（表格保留）---
    if prefer == "html":
        try:
            return _docx_via_mammoth_html(docx_path)
        except ImportError as e:
            print(f"[提示] 缺少依赖 ({e})，安装: pip install mammoth markdownify", file=sys.stderr)
            print("[提示] 回退到 mammoth 直接转 Markdown（表格会丢失）", file=sys.stderr)

    # --- 路线B: mammoth → Markdown（直接，表格丢失）---
    try:
        return _docx_via_mammoth_markdown(docx_path)
    except ImportError:
        print("[提示] mammoth 未安装，安装: pip install mammoth", file=sys.stderr)
        print("[提示] 回退到 docx2txt（结构全部丢失）", file=sys.stderr)

    # --- 路线C: docx2txt 纯文本（仅兜底）---
    try:
        return _docx_via_docx2txt(docx_path)
    except ImportError:
        print("[错误] 需要至少安装一个依赖: pip install mammoth markdownify docx2txt", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="将 DOCX 转换为 Markdown（保留表格结构）")
    parser.add_argument("input", type=str, help="输入 .docx 文件路径")
    parser.add_argument("-o", "--output", type=str, default=None, help="输出 .md 文件路径（默认同目录同名）")
    parser.add_argument("--stdout", action="store_true", help="输出到标准输出（不写文件）")
    parser.add_argument(
        "--mode", choices=["html", "direct"], default="html",
        help="'html' 推荐, 表格保留为 GFM pipe table; 'direct' 直接转 Markdown 但表格会丢失",
    )
    args = parser.parse_args()

    docx_path = Path(args.input)

    if not docx_path.exists():
        print(f"[错误] 文件不存在: {docx_path}", file=sys.stderr)
        sys.exit(1)

    if docx_path.suffix.lower() != ".docx":
        print(f"[错误] 输入文件必须是 .docx 格式，收到: {docx_path.suffix}", file=sys.stderr)
        sys.exit(1)

    markdown = convert_to_markdown(docx_path, prefer=args.mode)

    # --- 输出 ---
    if args.stdout:
        print(markdown)
        return

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = docx_path.with_suffix(".md")

    out_path.write_text(markdown, encoding="utf-8")
    print(f"[✓] 已写入: {out_path.resolve()} ({len(markdown)} 字符)", file=sys.stderr)

    # --- 快速检查: 是否包含表格？---
    table_count = markdown.count("\n|")
    if table_count > 0:
        print(f"[ℹ] 检测到约 {table_count} 个表格行，已保留为 Markdown pipe table 格式", file=sys.stderr)
    elif "mammoth" in sys.modules:
        print(f"[ℹ] 未检测到表格内容，如有表格请确认 DOCX 中使用的是标准表格", file=sys.stderr)


if __name__ == "__main__":
    main()
