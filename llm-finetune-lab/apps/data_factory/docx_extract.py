"""Word 文档提取：段落与表格按原始顺序交错输出，识别标题层级。

Word 制度文档通常用「标题 N」样式组织章节；也兼容正文里直接写
「第X章 / 一、二、」开头的伪标题（正则启发式，级别粗略）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 匹配「第X章/第一章」开头的行
_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百千零〇0-9]+章")
# 匹配「一、」「（一）」开头的行
_ENUM_RE = re.compile(r"^([一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)")


@dataclass
class Block:
    """提取结果的最小单元：标题 / 段落 / 表格。"""

    kind: str  # heading | para | table
    text: str
    level: int | None = None  # 仅 heading 有：1=章级，2=节级…

    def to_dict(self) -> dict:
        return {"kind": self.kind, "level": self.level, "text": self.text}


def _heading_level(paragraph) -> int | None:
    """标题级别：样式名优先，正文格式启发式兜底。"""
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    m = re.search(r"(?:heading|标题)\s*(\d)", name, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    text = paragraph.text.strip()
    if not text or len(text) > 40:
        return None
    if _CHAPTER_RE.match(text):
        return 1
    if _ENUM_RE.match(text) and len(text) < 30:
        return 2
    return None


def _table_text(table) -> str:
    """表格转文本：首行当表头，每行拼成「表头1：值1，表头2：值2」。"""
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    header = rows[0]
    lines = ["；".join(header)]
    for row in rows[1:]:
        pairs = []
        for i, value in enumerate(row):
            key = header[i] if i < len(header) and header[i] else f"列{i + 1}"
            if value:
                pairs.append(f"{key}：{value}")
        if pairs:
            lines.append("，".join(pairs))
    return "\n".join(lines)


def extract_docx(path: Path) -> list[Block]:
    """按文档原始顺序提取标题/段落/表格。"""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    blocks: list[Block] = []
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            level = _heading_level(para)
            if level is not None:
                blocks.append(Block("heading", text, level))
            else:
                blocks.append(Block("para", text))
        elif tag == "tbl":
            table = Table(child, doc)
            text = _table_text(table).strip()
            if text:
                blocks.append(Block("table", text))
    return blocks


def find_corpus_docx(corpus_dir: Path) -> Path | None:
    """corpus 目录里找 .docx：多个时取最新修改的那个。"""
    files = sorted(corpus_dir.glob("*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)
    files = [f for f in files if not f.name.startswith("~$")]  # 跳过 Word 锁文件
    return files[0] if files else None
