"""汇总格式化：终端文本 / 飞书 markdown / JSON 三种渲染。

输出契约：
- render_text：终端人读输出，无 emoji（避免 GBK 控制台编码问题）；
- render_lark：飞书 markdown，emoji 与全角缩进用于手机端阅读；
- render_json：结构化 JSON，供 pi / opencode 等 agent 做智能分析的对接面，
  datetimes 为本地日期的 ISO 字符串，ensure_ascii=False。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime

from .scanner import FileReport

# 单条任务超过该长度时截断加省略号（全文可通过 --json 查看）
MAX_ITEM_LEN = 80
# 截止日剩余天数不超过该值视为「临近」
DUE_SOON_DAYS = 3


def total_open(reports: list[FileReport]) -> int:
    """全部文件的未完成条目总数。"""
    return sum(r.open_count for r in reports)


def render_text(reports: list[FileReport], today: date | None = None) -> str:
    """终端人读输出：只列未完成任务。"""
    return _render_common(reports, header=lambda n_files, n_open: f"待办提醒 · {n_files} 个文件 · {n_open} 条未完成", today=today)


def render_lark(reports: list[FileReport], today: date | None = None) -> str:
    """飞书 markdown：只列未完成任务。"""
    return _render_common(
        reports,
        header=lambda n_files, n_open: f"📋 待办提醒 · {n_files} 个文件 · {n_open} 条未完成",
        today=today,
    )


def _render_common(
    reports: list[FileReport],
    *,
    header: Callable[[int, int], str],
    today: date | None = None,
) -> str:
    """终端与飞书共用的正文渲染，仅头部一行不同。"""
    today = today or date.today()
    n_open = total_open(reports)
    if n_open == 0:
        return header(len(reports), 0) + "\n\n无未完成任务"

    lines = [header(len(reports), n_open)]
    for report in reports:
        if report.open_count == 0:
            continue  # 只展示含未完成条目的文件
        lines.append("")
        lines.append(f"【{report.title}】{report.open_count} 条未完成{_due_hint(report.due, today)}")
        for section in report.sections:
            open_items = [i for i in section.items if not i.done]
            if not open_items:
                continue
            if section.title:
                lines.append(f"○ {section.title}")
            for item in open_items:
                # 顶层条目一个全角空格起头，每 2 空格缩进再进一级
                indent = "　" * (item.indent // 2 + 1)
                lines.append(f"{indent}• {_truncate(item.text)}")
    return "\n".join(lines)


def _due_hint(due: date | None, today: date) -> str:
    """截止日提示：逾期 / 临近标 ⚠️，正常给剩余天数。"""
    if due is None:
        return ""
    days = (due - today).days
    if days < 0:
        return f"（预期 {due:%m-%d}，已逾期 {-days} 天⚠️）"
    if days == 0:
        return f"（预期 {due:%m-%d}，今天到期⚠️）"
    if days <= DUE_SOON_DAYS:
        return f"（预期 {due:%m-%d}，仅剩 {days} 天⚠️）"
    return f"（预期 {due:%m-%d}，剩 {days} 天）"


def _truncate(text: str) -> str:
    """超长任务文本截断，中文字符按 1 计。"""
    return text if len(text) <= MAX_ITEM_LEN else text[:MAX_ITEM_LEN] + "…"


def render_json(reports: list[FileReport], todo_dir, today: date | None = None) -> str:
    """结构化 JSON 输出（agent 对接面）。"""
    today = today or date.today()
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "todo_dir": str(todo_dir),
        "total_open": total_open(reports),
        "total_done": sum(r.done_count for r in reports),
        "files": [_file_to_dict(r, today) for r in reports],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _file_to_dict(report: FileReport, today: date) -> dict:
    """FileReport 转 JSON 友好的 dict，补充截止日剩余天数。"""
    return {
        "path": str(report.path),
        "title": report.title,
        "created": report.created.isoformat() if report.created else None,
        "due": report.due.isoformat() if report.due else None,
        "due_days_left": (report.due - today).days if report.due else None,
        "open_count": report.open_count,
        "done_count": report.done_count,
        "sections": [
            {
                "title": s.title,
                "items": [
                    {"indent": i.indent, "done": i.done, "text": i.text} for i in s.items
                ],
            }
            for s in report.sections
        ],
    }
