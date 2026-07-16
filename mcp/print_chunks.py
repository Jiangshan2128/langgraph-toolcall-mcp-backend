#!/usr/bin/env python3
"""
打印文档分割后的 chunks 内容到文件，用于调试和检查分割效果。

使用方法:
    cd backend
    python mcp/print_chunks.py [文件路径或目录] [--output 输出文件] [--limit N]

示例:
    python mcp/print_chunks.py mcp/knowledge_base/documents/笔记.md
    python mcp/print_chunks.py mcp/knowledge_base/documents/ --output chunks.txt
    python mcp/print_chunks.py mcp/knowledge_base/documents/ --output chunks.txt --limit 5
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

# 添加 mcp 目录到路径
_mcp_dir = str(Path(__file__).resolve().parent)
if _mcp_dir not in sys.path:
    sys.path.insert(0, _mcp_dir)

from rag_kb.config import get_rag_config
from rag_kb.loader import load_file
from rag_kb.splitter import split_documents


def write_chunks_for_file(file_path: str, output_file, limit: int = None):
    """将单个文件的 chunks 写入文件。"""
    file_path = Path(file_path).resolve()
    
    if not file_path.exists():
        output_file.write(f"❌ 文件不存在: {file_path}\n")
        return 0
    
    if not file_path.is_file():
        output_file.write(f"❌ 不是文件: {file_path}\n")
        return 0
    
    output_file.write(f"\n{'='*60}\n")
    output_file.write(f"📄 文件: {file_path.name}\n")
    output_file.write(f"📁 完整路径: {file_path}\n")
    output_file.write(f"{'='*60}\n\n")
    
    # 1. 加载文档
    try:
        docs = load_file(file_path)
        output_file.write(f"✅ 加载成功，原始文档数: {len(docs)}\n")
    except Exception as e:
        output_file.write(f"❌ 加载失败: {e}\n")
        return 0
    
    if not docs:
        output_file.write("⚠️  没有加载到内容\n")
        return 0
    
    # 打印原始文档信息
    for i, doc in enumerate(docs):
        output_file.write(f"\n--- 原始文档 {i+1}/{len(docs)} ---\n")
        output_file.write(f"内容长度: {len(doc.page_content)} 字符\n")
        output_file.write(f"元数据: {doc.metadata}\n")
        output_file.write(f"内容预览 (前200字符):\n")
        output_file.write(doc.page_content[:200].replace('\n', ' ') + "...\n")
    
    # 2. 分割文档
    config = get_rag_config()
    chunks = split_documents(docs, config)
    
    output_file.write(f"\n{'='*60}\n")
    output_file.write(f"✂️  分割结果: {len(chunks)} 个 chunks\n")
    output_file.write(f"{'='*60}\n\n")
    
    if not chunks:
        output_file.write("⚠️  没有生成 chunks\n")
        return 0
    
    # 3. 写入每个 chunk
    display_count = min(limit, len(chunks)) if limit else len(chunks)
    
    for i, chunk in enumerate(chunks[:display_count], 1):
        output_file.write(f"\n{'─'*60}\n")
        output_file.write(f"📦 Chunk {i}/{len(chunks)}\n")
        output_file.write(f"{'─'*60}\n")
        output_file.write(f"长度: {len(chunk.page_content)} 字符\n")
        output_file.write(f"格式: {chunk.metadata.get('format', 'N/A')}\n")
        output_file.write(f"来源: {chunk.metadata.get('source', 'N/A')}\n")
        output_file.write(f"文件名: {chunk.metadata.get('file_name', 'N/A')}\n")
        # Show markdown header context
        for h_key in ("h1", "h2", "h3", "h4"):
            val = chunk.metadata.get(h_key)
            if val:
                output_file.write(f"标题({h_key}): {val}\n")
        output_file.write(f"\n📝 内容:\n")
        output_file.write(chunk.page_content)
        output_file.write("\n\n")
    
    if limit and len(chunks) > limit:
        output_file.write(f"\n... 还有 {len(chunks) - limit} 个 chunks 未显示\n")
    
    output_file.write(f"\n{'='*60}\n")
    output_file.write(f"总计: {len(chunks)} 个 chunks\n")
    output_file.write(f"{'='*60}\n\n")
    
    return len(chunks)


def write_chunks_for_directory(dir_path: str, output_file, limit_per_file: int = None):
    """将目录下所有文件的 chunks 写入文件。"""
    dir_path = Path(dir_path).resolve()
    
    if not dir_path.exists():
        output_file.write(f"❌ 目录不存在: {dir_path}\n")
        return 0
    
    if not dir_path.is_dir():
        output_file.write(f"❌ 不是目录: {dir_path}\n")
        return 0
    
    # 支持的文件扩展名
    supported_exts = {".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml",
                      ".yml", ".toml", ".cfg", ".ini", ".log", ".csv",
                      ".pdf", ".html", ".htm", ".docx"}
    
    # 收集所有文件
    files = [f for f in dir_path.rglob("*") 
             if f.is_file() and f.suffix.lower() in supported_exts]
    
    output_file.write(f"\n📂 目录: {dir_path}\n")
    output_file.write(f"📊 找到 {len(files)} 个支持类型的文件\n\n")
    
    if not files:
        output_file.write("⚠️  没有找到可处理的文件\n")
        output_file.write(f"支持的类型: {', '.join(sorted(supported_exts))}\n")
        return 0
    
    total_chunks = 0
    for file_path in sorted(files):
        chunks_count = write_chunks_for_file(str(file_path), output_file, limit=limit_per_file)
        total_chunks += chunks_count
        output_file.write("\n" + "="*60 + "\n\n")
    
    return total_chunks


def main():
    parser = argparse.ArgumentParser(
        description="打印文档分割后的 chunks 内容到文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python mcp/print_chunks.py mcp/knowledge_base/documents/笔记.md
  python mcp/print_chunks.py mcp/knowledge_base/documents/ --output chunks.txt
  python mcp/print_chunks.py mcp/knowledge_base/documents/ --output chunks.txt --limit 5
        """
    )
    parser.add_argument(
        "path",
        help="文件路径或目录路径"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="输出文件路径（默认输出到控制台）"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="每个文件最多显示的 chunks 数量（默认显示全部）"
    )
    
    args = parser.parse_args()
    
    target_path = Path(args.path)
    
    # 确定输出目标
    if args.output:
        output_path = Path(args.output)
        # 确保输出目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_file = open(output_path, 'w', encoding='utf-8')
        print(f"📝 输出到文件: {output_path.absolute()}")
    else:
        output_file = sys.stdout
    
    try:
        # 写入文件头
        output_file.write(f"Chunks 导出报告\n")
        output_file.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        output_file.write(f"来源路径: {target_path.absolute()}\n")
        output_file.write(f"{'='*60}\n")
        
        if target_path.is_file():
            total = write_chunks_for_file(str(target_path), output_file, limit=args.limit)
            print(f"✅ 完成，共 {total} 个 chunks")
        elif target_path.is_dir():
            total = write_chunks_for_directory(str(target_path), output_file, limit_per_file=args.limit)
            print(f"✅ 完成，共 {total} 个 chunks")
        else:
            print(f"❌ 路径不存在: {target_path}")
            sys.exit(1)
    finally:
        if args.output:
            output_file.close()
            print(f"✅ 文件已保存: {Path(args.output).absolute()}")


if __name__ == "__main__":
    main()