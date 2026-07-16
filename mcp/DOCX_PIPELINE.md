# DOCX 文档加载与分片流程

> 适用版本：AI Note Backend — `mcp/rag_kb/`
>
> 核心文件：`loader.py`, `splitter.py`

---

## 总览

```
                           ┌──────────────────────────┐
                           │     DOCX / MD / TXT      │
                           │        … 等文件           │
                           └────────────┬─────────────┘
                                        │
                                        ▼
                              ┌──────────────────┐
                              │    loader.py     │
                              │  格式路由 +      │
                              │  DOCX 后处理管线  │
                              └────────┬─────────┘
                                       │
                                  Document[]
                              (page_content=Markdown
                               metadata={"format": "markdown"})
                                       │
                                       ▼
                              ┌──────────────────┐
                              │   splitter.py    │
                              │  Markdown →      │
                              │  HeaderSplitter  │
                              │  → ItemSplitter  │
                              │  → Recursive     │
                              └────────┬─────────┘
                                       │
                                  Chunk[]
                              (每 chunk 带 h1/h2
                               面包屑 + 编号前缀)
                                       │
                                       ▼
                              ┌──────────────────┐
                              │   Qdrant 向量库   │
                              └──────────────────┘
```

---

## 一、加载阶段（loader.py）

### 1.1 格式路由

```
load_file(path)
  │
  ├─ .txt / .md / .py / .json … → _load_text()
  │     └─ .md 标记 format=markdown，其余 format=text
  │
  ├─ .csv  → _load_csv()   (LangChain CSVLoader)
  ├─ .pdf  → _load_pdf()   (PyPDFLoader)
  ├─ .html → _load_html()  (BSHTMLLoader)
  │
  └─ .docx → _load_docx()
              │
              ├─ Pandoc 已安装? ──→ _load_docx_via_pandoc()  ← 主线
              │
              └─ Pandoc 未安装 ──→ Docx2txtLoader (纯文本, format=text)
```

### 1.2 DOCX → Markdown 后处理管线

```
DOCX 二进制文件
      │
      ▼
┌─────────────────────────────────────────┐
│ ① Pandoc 转换                            │
│   $ pandoc file.docx -t gfm --wrap=preserve
│                                          │
│   输出：GFM Markdown                      │
│   • 标题层级 → # ## ### ####             │
│   • 简单表格 → pipe table (原生)          │
│   • 复杂表格 (colspan/colgroup) → HTML <table>
│   • 编号列表 → 1) 2) 3) + 缩进子项        │
│   • TOC 目录 → 链接集群或 # 目录 标题      │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ ② HTML 表格 → GFM pipe table            │
│   _html_table_to_pipe_table()           │
│                                          │
│   • 剥离 <colgroup> <caption>           │
│   • colspan 展开（追加空 <td>）           │
│   • rowspan 简化（内容留在首行）           │
│   • 实体解码，| 转义                      │
│   • <tr> → | cell | cell |              │
│   • 首行作为表头                          │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ ③ TOC 目录剥离                           │
│   _strip_markdown_toc()                 │
│                                          │
│   Pass 1 — 标题匹配：                     │
│     匹配 # 目录 / # Table of Contents     │
│     → 删除到下一个同级标题                 │
│                                          │
│   Pass 2 — 模式匹配：                     │
│     正则识别 [text [N]](#anchor) 链接      │
│     连续 ≥5 条聚簇 → 整簇删除              │
│     处理 > 块引用分隔符                    │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ ④ 转换噪音清理                           │
│   _clean_markdown_noise()               │
│                                          │
│   • <span class="mark">…</span> → 保留内容│
│   • <img …> → 删除（图片不在知识库内）      │
│   • <!-- … --> → 删除（分页符残留）        │
│   • 连续 3+ 空行 → 压缩为 2 空行           │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ ⑤ 列表层级标准化                         │
│   _normalize_list_hierarchy()           │
│                                          │
│   Pass 1 — 子项转 bullet：               │
│     缩进的 N) → - (保留 4 空格层级)        │
│     顶层的 N) → 保持编号                  │
│                                          │
│   Pass 2 — 编号连续化：                   │
│     heading → 计数器归零                  │
│     连续顶层条目 → 1) 2) 3) 4) …         │
│     不再出现 3) 1) 2) 这种跳号             │
└────────────────┬────────────────────────┘
                 │
                 ▼
        Document(
          page_content=清洁 Markdown,
          metadata={
            "source": "/path/to/file.docx",
            "file_name": "file.docx",
            "format": "markdown",       ← 关键标识
          }
        )
```

### 1.3 后处理效果对比

```
原始 Pandoc 输出:                    最终 Markdown:
─────────────────                   ─────────────
<table>                             | 角色 | 承担方 |
<colgroup>                    →     | --- | --- |
<col style="width: 21%">           | 机芯 | 智冠华 |
...

[第1章 [4]](#第1章)          →     (已删除)
> [1.1 [4]](#1.1)          …

<span class="mark">25Gbps    →     25Gbps

1) 数量：1块                      1) 数量：1块
2) 总线：PXIe                      2) 总线：PXIe
1) 工作模式：                  →    3) 工作模式：
    1) 视频采集                        - 视频采集
    2) 视频转换                        - 视频转换
```

---

## 二、分片阶段（splitter.py）

### 2.1 路由决策

```
split_documents(docs, config)
  │
  ├─ doc.metadata["format"] == "markdown"
  │     │
  │     └─→ _split_markdown_doc()
  │           │
  │           ├─① MarkdownHeaderTextSplitter
  │           │   按 # ## ### #### 切 section
  │           │   → 每个 section 带 h1/h2/h3/h4 metadata
  │           │
  │           ├─② Section ≤ CHUNK_SIZE?
  │           │    YES → 直接保留（MarkdownHeaderTextSplitter 自带元数据）
  │           │
  │           ├─③ Section > CHUNK_SIZE + 有 ≥2 个顶层编号条目?
  │           │    YES → _split_by_numbered_items()
  │           │    │   • 在 \nN) 边界切分
  │           │    │   • 小条目组合进一个 chunk
  │           │    │   • 单条目超大 → 递归切分
  │           │    │   • 每个 chunk 前置 # h1 \n ## h2 面包屑
  │           │    │
  │           │    NO → RecursiveCharacterTextSplitter
  │           │        • 盲切 + 面包屑前缀
  │           │
  │           └─④ 后处理: chunk_index 编号, source/file_name 补齐
  │
  └─ format != "markdown"
        └─→ RecursiveCharacterTextSplitter 直接切
```

### 2.2 编号条目感知拆分（核心创新）

```
Section 内容（不含标题行）：
  ┌──────────────────────────────────────┐
  │ 采用 UltraScale+ 系列 FPGA...         │  ← intro (前导 prose)
  │                                      │
  │ 1) 功能要求                           │  ← item 1
  │    - 支持监听 A818 数据；              │     含缩进子项
  │    - 支持监听 DVI 数据；               │
  │                                      │
  │ 2) 轻便型 A818 协议监听模块性能：       │  ← item 2
  │    - 尺寸 ≤120mm×120mm×40mm          │
  │    - 存储 ≥8T                        │
  │                                      │
  │ 3) 轻便型 DVI 协议监听模块性能：        │  ← item 3
  │    - ...                             │
  └──────────────────────────────────────┘

_split_by_numbered_items() 行为：

  1. 正则 \n(?=\d+\)\s+\S) 在条目边界切开
     → [intro, "1) 功能要求\n  - ...", "2) 轻便型...", "3) 轻便型..."]

  2. 贪心组合：前 N 个条目总长 ≤ CHUNK_SIZE → 放一个 chunk
     下一个条目开始 → 新 chunk

  3. 每个 chunk 前置面包屑：
     # A818模块验证设备
     ## 数据监听模块

     1) 功能要求
        - 支持监听 A818 数据；
     ...

  4. 单条目超限（如视频转换验证装置的条目 1) 有 1600+ 字符）：
     → RecursiveCharacterTextSplitter + 面包屑前缀
```

### 2.3 Chunk Metadata 示例

```python
{
    "source":      "/docs/A818.docx",      # 原始文件路径
    "file_name":   "A818.docx",            # 文件名
    "format":      "markdown",             # 格式标记
    "h1":          "A818模块验证设备",       # 一级标题
    "h2":          "A818接收模块测试板卡",    # 二级标题
    "chunk_index": 1,                      # 序号
}
```

检索时可以拿到完整溯源链：**文件 → 一级标题 → 二级标题 → 具体条目**。

---

## 三、文档类型适配

| 文档类型 | 特征 | 分片路径 |
|---------|------|---------|
| **章节叙述型** | h1→h2→h3→h4 深层级, 段落为主 | ①→②→直接保留 |
| 例：diaocang.docx | 9 章, 47 标题行, 表格多 | 40 chunks, 全部由 h2/h3 边界承载 |
| **条目清单型** | h1→h2 浅层级, 编号 `N)` 为主 | ①→②→③ 条目感知拆分 |
| 例：A818.docx | 3 章, 143 编号条目, 无 h3 | 27 chunks, 条目边界 + 面包屑 |
| **纯文本** | 无结构信息 | RecursiveCharacterTextSplitter 盲切 |

---

## 四、兜底策略

```
loader.py                          splitter.py
─────────                          ───────────
Pandoc 不可用                       MarkdownHeaderTextSplitter 异常
  → Docx2txtLoader (纯文本)           → 整文档当单个 chunk
  → format=text                      → RecursiveCharacterTextSplitter

编号条目不足 2 个                  单条目仍然超大
  → RecursiveCharacterTextSplitter   → RecursiveCharacterTextSplitter
    (盲切 + 面包屑)                      (盲切 + 面包屑)
```

每一层都有 fallback，不会因为一个文档格式奇特就整条管线挂掉。

---

## 五、关键设计原则

1. **加载与分片解耦** — loader 只管产出干净的 Markdown + 标注 `format`，splitter 按 format 路由到不同策略，两者互不干扰。

2. **保留溯源信息** — 每个 chunk 通过 `h1/h2` metadata + 面包屑前缀，在检索结果中能唯一定位到"哪个文件 > 哪个设备 > 哪个模块 > 哪个需求条目"。

3. **条目原子性优先** — 编号需求条目是语义原子，不应被盲切打散。只有单个条目超过 chunk_size 时，才降级为字符切分。

4. **后处理优于预处理** — 不在 DOCX 解析阶段修改 Word 结构，而是在 Pandoc 输出的文本层做"翻译"（HTML→pipe table, 缩进编号→bullet, 跳号→连续编号）。
