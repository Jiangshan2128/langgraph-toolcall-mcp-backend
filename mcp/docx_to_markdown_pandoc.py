#!/usr/bin/env python3
"""
DOCX → Markdown 转换脚本（Pandoc 版）

使用 Pandoc 将 Word 文档转为 Markdown，保留标题层级、表格、列表、脚注等完整结构。
自动将 Pandoc 残留的 HTML 表格转为 Markdown pipe table。

依赖:
    1. Pandoc: winget install JohnMacFarlane.Pandoc
    2. Python 标准库即可（无需额外 pip 包）

用法:
    python docx_to_markdown_pandoc.py <input.docx>
    python docx_to_markdown_pandoc.py <input.docx> -o output.md
    python docx_to_markdown_pandoc.py <input.docx> --stdout
    python docx_to_markdown_pandoc.py <input.docx> --gfm           # GitHub 风味 Markdown（默认）
    python docx_to_markdown_pandoc.py <input.docx> --commonmark    # CommonMark 标准
    python docx_to_markdown_pandoc.py <input.docx> --no-clean-html # 保留原始 HTML，不转 pipe table
"""

import argparse
import html
import re
import shutil
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
#  HTML 表格 → Markdown pipe table 转换器
# ---------------------------------------------------------------------------

def _html_table_to_pipe_table(html_text: str) -> str:
    """
    将 HTML <table> 转为 GFM pipe table。
    处理 <thead>/<tbody>/<tr>/<th>/<td>，剥离 <colgroup>、样式等。
    支持 <br> 转成行内换行。
    """
    # 1) 展开合并单元格（rowspan/colspan）—— 简单策略：复制到相邻格
    html_text = _expand_cell_spans(html_text)

    # 2) 清理 colgroup / style / class 等噪音
    html_text = re.sub(r'<colgroup[^>]*>.*?</colgroup>', '', html_text, flags=re.DOTALL)
    html_text = re.sub(r'<col[^>]*/?>', '', html_text)
    html_text = re.sub(r'<caption[^>]*>.*?</caption>', '', html_text, flags=re.DOTALL)

    # 3) 逐 <table> 替换
    def _replace_table(m: re.Match) -> str:
        return _convert_one_table(m.group(0))

    return re.sub(r'<table[^>]*>(.*?)</table>', _replace_table, html_text, flags=re.DOTALL)


def _expand_cell_spans(html_text: str) -> str:
    """将 rowspan/colspan 的 td/th 复制到后续行/列。"""
    expanded = html_text

    # -- colSpan → 在同一 <tr> 内追加 N-1 个空 <td>
    def _expand_colspan(m: re.Match) -> str:
        tag = m.group(0)
        n = int(m.group(1))
        if n <= 1:
            return tag
        # 去掉 colspan 属性
        clean = re.sub(r'\s*colspan\s*=\s*["\']?\d+["\']?', '', tag, flags=re.IGNORECASE)
        return clean + "<td></td>" * (n - 1)

    expanded = re.sub(
        r'<(td|th)([^>]*)\s+colspan\s*=\s*["\']?(\d+)["\']?',
        _expand_colspan,
        expanded,
        flags=re.IGNORECASE,
    )

    # -- rowSpan → 记录并在后续行插入 <td>
    #    简化策略：直接去掉 rowspan，单元格内容留在原位
    expanded = re.sub(
        r'\s*rowspan\s*=\s*["\']?\d+["\']?',
        '',
        expanded,
        flags=re.IGNORECASE,
    )

    return expanded


def _convert_one_table(table_html: str) -> str:
    """转换单个 <table> 为 pipe table。"""
    rows: list[list[str]] = []
    is_thead = False

    # 收集所有行
    tr_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
    cell_pattern = re.compile(r'<(th|td)([^>]*)>(.*?)</\1>', re.DOTALL | re.IGNORECASE)

    for tr in tr_pattern.finditer(table_html):
        cells = []
        for cell in cell_pattern.finditer(tr.group(1)):
            content = _clean_cell_content(cell.group(3))
            cells.append(content)
        if cells:
            # 判断是否 thead（检查 tr 是否在 thead 内，或 cell 是 th）
            in_thead = bool(re.search(r'<thead', table_html, re.IGNORECASE))
            # 简单策略：第一行有 <th> 就是表头
            if cell_pattern.search(tr.group(1)):
                tag_match = cell_pattern.search(tr.group(1))
                if tag_match and tag_match.group(1).lower() == 'th':
                    if not rows:
                        is_thead = True
            rows.append(cells)

    if not rows:
        return ''

    # 确定列数
    max_cols = max(len(r) for r in rows)
    # 补齐列
    for r in rows:
        while len(r) < max_cols:
            r.append('')

    lines: list[str] = []

    # 表头行
    header = '| ' + ' | '.join(rows[0]) + ' |'
    lines.append(header)

    # 分隔行
    sep = '|' + '|'.join(' --- ' for _ in range(max_cols)) + '|'
    lines.append(sep)

    # 数据行
    for row in rows[1:]:
        data = '| ' + ' | '.join(row) + ' |'
        lines.append(data)

    return '\n' + '\n'.join(lines) + '\n'


def _clean_cell_content(raw: str) -> str:
    """清理单元格内容：去标签、解实体、trim。"""
    # <br> → 空格
    text = re.sub(r'<br\s*/?>', ' ', raw, flags=re.IGNORECASE)
    # 去掉其余 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # HTML 实体解码
    text = html.unescape(text)
    # 合并空白
    text = re.sub(r'\s+', ' ', text).strip()
    # 转义 pipe（避免破坏 Markdown 表格语法）
    text = text.replace('|', r'\|')
    return text


# ---------------------------------------------------------------------------
#  去除目录
# ---------------------------------------------------------------------------

def strip_toc(markdown: str) -> str:
    """Remove the table-of-contents section from Markdown.

    Two-pass detection:

    1.  Heading-based — ``# 目录`` / ``# Table of Contents``.
    2.  Pattern-based — clusters of ``[text [N]](#anchor)`` TOC links
        (≥5 consecutive entries), with or without ``>`` blockquote prefix.
    """
    lines = markdown.split('\n')
    result: list[str] = []

    # ----------------------------------------------------------------
    #  Pass 1: heading-based
    # ----------------------------------------------------------------
    in_heading_toc = False
    toc_heading_level = 0

    for line in lines:
        m = re.match(
            r'^(#{1,4})\s*(目\s*录|Table\s+of\s+Contents|TOC)\s*$',
            line, re.IGNORECASE,
        )
        if m:
            in_heading_toc = True
            toc_heading_level = len(m.group(1))
            continue

        if in_heading_toc:
            heading = re.match(r'^(#{1,4})\s+', line)
            if heading and len(heading.group(1)) <= toc_heading_level:
                in_heading_toc = False

        if not in_heading_toc:
            result.append(line)

    # ----------------------------------------------------------------
    #  Pass 2: pattern-based (link cluster with page numbers)
    # ----------------------------------------------------------------
    _TOC_RE = re.compile(
        r'^(>\s*)?\[.+?\s+\[\d+\]\(#[^)]*\)\]\(#[^)]*\)\s*$'
    )

    # Mark which lines match TOC pattern
    toc_set: set[int] = set()
    for i, line in enumerate(result):
        if _TOC_RE.match(line):
            toc_set.add(i)

    # Find contiguous clusters of ≥5 TOC lines and strip them
    result2: list[str] = []
    i = 0
    while i < len(result):
        if i in toc_set:
            cluster_end = i
            toc_count = 1
            j = i + 1
            while j < len(result):
                if j in toc_set:
                    toc_count += 1
                    cluster_end = j
                    j += 1
                elif result[j].strip() in ('', '>'):
                    j += 1
                else:
                    break
            if toc_count >= 5:
                i = cluster_end + 1
                while i < len(result) and result[i].strip() == '':
                    i += 1
                continue

        result2.append(result[i])
        i += 1

    return '\n'.join(result2)


# ---------------------------------------------------------------------------
#  Pandoc 调用
# ---------------------------------------------------------------------------

def check_pandoc() -> str | None:
    pandoc_path = shutil.which("pandoc")
    if not pandoc_path:
        return None
    try:
        result = subprocess.run(
            ["pandoc", "--version"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.splitlines()[0] if result.stdout else "unknown"
    except (subprocess.CalledProcessError, OSError):
        return None


def convert_via_pandoc(
    docx_path: Path,
    output_path: Path | None = None,
    to_format: str = "gfm",
    extra_args: list[str] | None = None,
) -> str:
    cmd = ["pandoc", str(docx_path), "-t", to_format, "--wrap=preserve"]

    if extra_args:
        cmd.extend(extra_args)

    if output_path:
        cmd.extend(["-o", str(output_path)])
        subprocess.run(cmd, check=True)
        return output_path.read_text(encoding="utf-8")
    else:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="将 DOCX 转换为 Markdown（Pandoc 引擎，HTML 表格自动转 pipe table）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python docx_to_markdown_pandoc.py 文档.docx\n"
            "  python docx_to_markdown_pandoc.py 文档.docx --stdout\n"
            "  python docx_to_markdown_pandoc.py 文档.docx -o 文档.md\n"
            "  python docx_to_markdown_pandoc.py 文档.docx --no-clean-html  # 保留原始 HTML\n"
        ),
    )
    parser.add_argument("input", type=str, help="输入 .docx 文件路径")
    parser.add_argument("-o", "--output", type=str, default=None, help="输出 .md 文件路径（默认同目录同名）")
    parser.add_argument("--stdout", action="store_true", help="输出到标准输出（不写文件）")
    parser.add_argument("--no-clean-html", action="store_true", help="不清理 HTML 表格，保留 Pandoc 原始输出")
    parser.add_argument("--strip-toc", action="store_true", help="删除目录部分（从'# 目录'到下一个同级标题）")

    fmt_group = parser.add_argument_group("输出 Markdown 风格")
    fmt_group.add_argument("--gfm", action="store_true", help="GitHub 风味 Markdown（默认）")
    fmt_group.add_argument("--markdown", action="store_true", help="Pandoc Markdown（扩展语法）")
    fmt_group.add_argument("--commonmark", action="store_true", help="严格 CommonMark 标准")
    fmt_group.add_argument("--commonmark_x", action="store_true", help="CommonMark + 有用扩展")

    opt_group = parser.add_argument_group("Pandoc 选项")
    opt_group.add_argument("--toc", action="store_true", help="生成目录")
    opt_group.add_argument("--number-sections", action="store_true", help="章节编号")
    opt_group.add_argument("--raw-quotes", action="store_true", help="保留智能引号")
    opt_group.add_argument("--extract-media", type=str, metavar="DIR", help="提取图片到指定目录")
    opt_group.add_argument("--wrap", type=str, choices=["auto", "none", "preserve"], default="preserve",
                          help="文本换行策略（默认 preserve）")
    opt_group.add_argument("--extra", type=str, action="append", default=[],
                          help="透传额外 pandoc 参数，如: --extra=--resource-path=./images")

    args = parser.parse_args()
    docx_path = Path(args.input)

    if not docx_path.exists():
        print(f"[错误] 文件不存在: {docx_path}", file=sys.stderr)
        sys.exit(1)
    if docx_path.suffix.lower() != ".docx":
        print(f"[错误] 必须是 .docx 格式，收到: {docx_path.suffix}", file=sys.stderr)
        sys.exit(1)

    version = check_pandoc()
    if version is None:
        print("[错误] 未找到 pandoc。安装: winget install JohnMacFarlane.Pandoc", file=sys.stderr)
        sys.exit(1)

    to_map = {"gfm": "gfm", "markdown": "markdown", "commonmark": "commonmark", "commonmark_x": "commonmark_x"}
    selected = [k for k in to_map if getattr(args, k)]
    if len(selected) > 1:
        print(f"[错误] 只能指定一种输出格式: {selected}", file=sys.stderr)
        sys.exit(1)
    to_format = selected[0] if selected else "gfm"

    extra_args = []
    if args.toc:
        extra_args.append("--toc")
    if args.number_sections:
        extra_args.append("--number-sections")
    if args.raw_quotes:
        extra_args.append("--smart")
    if args.extract_media:
        extra_args.extend(["--extract-media", args.extract_media])
        Path(args.extract_media).mkdir(parents=True, exist_ok=True)
    extra_args.append(f"--wrap={args.wrap}")
    for e in args.extra:
        extra_args.append(e)

    try:
        if args.stdout:
            md = convert_via_pandoc(docx_path, to_format=to_format, extra_args=extra_args)
            if not args.no_clean_html:
                md = _html_table_to_pipe_table(md)
            if args.strip_toc:
                md = strip_toc(md)
            print(md, end="")
            return

        out_path = Path(args.output) if args.output else docx_path.with_suffix(".md")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        md = convert_via_pandoc(docx_path, output_path=out_path, to_format=to_format, extra_args=extra_args)

        # --- HTML 表格 → pipe table ---
        if not args.no_clean_html:
            cleaned = _html_table_to_pipe_table(md)
            if cleaned != md:
                md = cleaned
                print("[✓] HTML 表格已转换为 Markdown pipe table", file=sys.stderr)

        # --- 删除目录 ---
        if args.strip_toc:
            before = len(md)
            md = strip_toc(md)
            after = len(md)
            if after < before:
                print(f"[✓] 已删除目录部分（减少 {before - after} 字符）", file=sys.stderr)

        # 写入最终结果
        out_path.write_text(md, encoding="utf-8")

        print(f"[✓] Pandoc ({version})", file=sys.stderr)
        print(f"[✓] 格式: {to_format}", file=sys.stderr)
        print(f"[✓] 已写入: {out_path.resolve()}", file=sys.stderr)

        # 统计
        lines = md.splitlines()
        markdown_table_rows = sum(1 for l in lines if l.startswith("|"))
        html_table_tags = sum(1 for l in lines if "<table" in l.lower() or "</table" in l.lower())
        heading_count = sum(1 for l in lines if l.startswith("#"))
        print(
            f"[ℹ] 统计: {len(lines)} 行, "
            f"{heading_count} 个标题, "
            f"{markdown_table_rows} 行 pipe table, "
            f"{html_table_tags} 个残留 HTML <table> 标签",
            file=sys.stderr,
        )

    except subprocess.CalledProcessError as e:
        print(f"[错误] Pandoc 执行失败 (exit {e.returncode})", file=sys.stderr)
        if e.stderr:
            print(f"[错误详情]\n{e.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[错误] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
