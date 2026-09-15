"""结构感知分块：尽量按章节边界切块，保证每个 chunk 语义自洽。

规则：
1. 遇到标题即切换章节上下文（section_path）；
2. 段落累积到 target 字数即成块；不足 min 的并入下一块（同章节内）；
3. 超过 max 的段落按句号切分；
4. chunk_id 用内容哈希，保证重跑稳定、生成阶段可断点续跑。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from apps.data_factory.docx_extract import Block

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。；！？\n])")


@dataclass
class Chunk:
    chunk_id: str
    section_path: str  # 形如 "第三章 > 考勤管理 > 迟到处理"
    text: str

    def to_dict(self) -> dict:
        return {"chunk_id": self.chunk_id, "section_path": self.section_path, "text": self.text}


def _make_id(section_path: str, text: str) -> str:
    digest = hashlib.sha1(f"{section_path}|{text[:80]}|{len(text)}".encode("utf-8")).hexdigest()
    return digest[:12]


def _split_long_text(text: str, max_size: int) -> list[str]:
    if len(text) <= max_size:
        return [text]
    sentences = _SENTENCE_SPLIT_RE.split(text)
    parts: list[str] = []
    buf = ""
    for sentence in sentences:
        if buf and len(buf) + len(sentence) > max_size:
            parts.append(buf)
            buf = sentence
        else:
            buf += sentence
    if buf:
        parts.append(buf)
    return parts


def chunk_blocks(
    blocks: list[Block],
    target: int = 400,
    min_size: int = 120,
    max_size: int = 600,
) -> list[Chunk]:
    """把提取结果切成语义自洽的块。"""
    chunks: list[Chunk] = []
    sections: list[str] = []  # 当前章节路径栈
    buf: list[str] = []
    buf_chars = 0

    def flush() -> None:
        nonlocal buf, buf_chars
        if not buf:
            return
        text = "\n".join(buf).strip()
        buf, buf_chars = [], 0
        if not text:
            return
        section_path = " > ".join(sections) if sections else "全文"
        for part in _split_long_text(text, max_size):
            chunks.append(Chunk(_make_id(section_path, part), section_path, part))

    for block in blocks:
        if block.kind == "heading":
            flush()
            # 章节层级：弹出到当前标题的父级再入栈
            level = block.level or 1
            while len(sections) >= level:
                sections.pop()
            sections.append(block.text[:40])
            continue

        text = block.text
        if buf_chars + len(text) > target:
            flush()
        buf.append(text)
        buf_chars += len(text)
        if buf_chars >= target:
            flush()

    flush()

    # 太短的尾部块并入前一块（同 section 或跨 section 都允许，标记用前一块的路径）
    merged: list[Chunk] = []
    for chunk in chunks:
        if merged and len(chunk.text) < min_size:
            prev = merged[-1]
            merged[-1] = Chunk(_make_id(prev.section_path, prev.text + "\n" + chunk.text),
                               prev.section_path, prev.text + "\n" + chunk.text)
        else:
            merged.append(chunk)
    return merged
