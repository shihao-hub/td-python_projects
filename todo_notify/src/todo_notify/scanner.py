"""扫描待办目录，解析 Markdown 复选框，产出结构化 FileReport。

解析规则（确定性正则，一期方案）：
- fenced code block（``` 与 ~~~ 围栏）内的内容一律跳过，避免代码示例误报；
- 复选框行：`- [ ]` 未完成、`- [x]` / `- [X]` 已完成，缩进保留以体现子任务层级；
- 章节归属：距该行最近的前置 `#{1,6}` 标题行；
- 文件级元信息 best-effort：文件名尾部 `-YYYYMMDD` → 创建日期；
  正文表格中 `| 预期完成 | YYYY-MM-DD |` → 截止日期，缺失不影响扫描。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# 复选框行：捕获缩进、勾选状态（空格=未完成，x/X=已完成）、任务文本
CHECKBOX_RE = re.compile(r"^(\s*)-\s\[([ xX])\]\s+(.+)$")
# ATX 标题行（#{1,6}），用于章节归属
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
# 围栏行：``` 或 ~~~ 连续 3 个以上（开围栏后可跟语言标注）
FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})")
# 闭围栏：同类字符且长度不小于开围栏，行尾不允许有其他内容
FENCE_CLOSE_RE = re.compile(r"^(`{3,}|~{3,})\s*$")
# 文件名尾部日期：形如「主题-20260911」
FILENAME_DATE_RE = re.compile(r"-(\d{8})$")
# 元信息表中的截止日期行：| 预期完成 | 2026-09-18 |
DUE_RE = re.compile(r"\|\s*预期完成\s*\|\s*(\d{4}-\d{2}-\d{2})")


@dataclass
class Item:
    """一条复选框任务。"""

    indent: int  # 原始缩进空格数，2 空格一级子任务
    text: str  # 去掉复选框标记后的任务文本
    done: bool


@dataclass
class Section:
    """一个标题章节及其下的任务条目。"""

    title: str
    items: list[Item] = field(default_factory=list)


@dataclass
class FileReport:
    """单个 Markdown 文件的扫描结果。"""

    path: Path
    title: str  # 文件名去扩展名，如「PG慢SQL优化与监控告警建设-20260911」
    created: date | None  # 文件名尾部日期，best-effort
    due: date | None  # 元信息表「预期完成」，best-effort
    sections: list[Section] = field(default_factory=list)
    open_count: int = 0
    done_count: int = 0


def scan(todo_dir: Path) -> list[FileReport]:
    """递归扫描目录下所有 .md 文件，返回 FileReport 列表（按文件名排序）。"""
    reports: list[FileReport] = []
    for md in sorted(todo_dir.rglob("*.md")):
        if md.is_file():
            reports.append(_parse_file(md))
    return reports


def _parse_file(path: Path) -> FileReport:
    """解析单个 Markdown 文件。"""
    # utf-8-sig 兼容带 BOM 的文件，无 BOM 时行为与 utf-8 一致
    text = path.read_text(encoding="utf-8-sig", errors="replace")

    report = FileReport(
        path=path,
        title=path.stem,
        created=_date_from_stem(path.stem),
        due=_due_from_text(text),
    )
    # 文件开头可能没有标题就出现复选框，用空章节兜底
    section = Section(title="")
    fence_char: str | None = None  # 当前围栏字符，None 表示不在代码块内
    fence_len = 0

    for line in text.splitlines():
        stripped = line.lstrip()

        if fence_char is None:
            m = FENCE_OPEN_RE.match(stripped)
            if m:
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                continue
        else:
            m = FENCE_CLOSE_RE.match(stripped)
            if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len:
                fence_char = None
            continue  # 代码块内的行不参与解析

        m = HEADING_RE.match(line)
        if m:
            # 新章节：仅在已有内容时才落盘上一节，避免空章节堆积
            if section.items or section.title:
                report.sections.append(section)
            section = Section(title=m.group(2).strip())
            continue

        m = CHECKBOX_RE.match(line)
        if m:
            item = Item(
                indent=len(m.group(1)),
                text=m.group(3).strip(),
                done=m.group(2) in "xX",
            )
            section.items.append(item)
            if item.done:
                report.done_count += 1
            else:
                report.open_count += 1

    if section.items or section.title:
        report.sections.append(section)
    return report


def _date_from_stem(stem: str) -> date | None:
    """从文件名尾部 `-YYYYMMDD` 解析创建日期。"""
    m = FILENAME_DATE_RE.search(stem)
    if not m:
        return None
    raw = m.group(1)
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None  # 形如 20261399 的非法日期，静默放弃


def _due_from_text(text: str) -> date | None:
    """从正文元信息表解析「预期完成」日期。"""
    m = DUE_RE.search(text)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None
