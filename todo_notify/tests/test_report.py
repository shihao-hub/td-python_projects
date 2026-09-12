"""report 渲染测试：截断 / 统计 / 截止日提示 / JSON 结构。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from todo_notify.report import (
    MAX_ITEM_LEN,
    render_json,
    render_lark,
    render_text,
    total_open,
)
from todo_notify.scanner import FileReport, Item, Section, scan

TODAY = date(2026, 9, 13)


def _report(**kwargs) -> FileReport:
    """构造最小 FileReport。"""
    defaults = dict(
        path=Path("D:/fake/x-20260901.md"),
        title="x-20260901",
        created=date(2026, 9, 1),
        due=None,
        sections=[],
        open_count=0,
        done_count=0,
    )
    defaults.update(kwargs)
    return FileReport(**defaults)


def _one_item_report(text: str, due: date | None = None) -> FileReport:
    return _report(
        due=due,
        sections=[Section(title="章", items=[Item(indent=0, text=text, done=False)])],
        open_count=1,
    )


def test_truncate_long_item() -> None:
    """超 80 字符截断加省略号，正好 80 不截。"""
    long_text = "长" * (MAX_ITEM_LEN + 10)
    out = render_lark([_one_item_report(long_text)], today=TODAY)
    assert "长" * MAX_ITEM_LEN + "…" in out
    assert "长" * (MAX_ITEM_LEN + 1) not in out

    exact = "好" * MAX_ITEM_LEN
    out = render_lark([_one_item_report(exact)], today=TODAY)
    assert "好" * MAX_ITEM_LEN in out
    assert "…" not in out


def test_header_counts() -> None:
    """头部行包含文件数与未完成总数。"""
    r1 = _one_item_report("任务a")
    r2 = _one_item_report("任务b")
    r2.sections.append(Section(title="章2", items=[Item(0, "完成的", True)]))
    r2.done_count = 1
    out = render_text([r1, r2], today=TODAY)
    assert "2 个文件 · 2 条未完成" in out


def test_due_hints() -> None:
    """逾期 / 临近（<=3 天）标 ⚠️，正常只给剩余天数。"""
    overdue = render_lark([_one_item_report("t", due=date(2026, 9, 10))], today=TODAY)
    assert "已逾期 3 天⚠️" in overdue
    soon = render_lark([_one_item_report("t", due=date(2026, 9, 15))], today=TODAY)
    assert "仅剩 2 天⚠️" in soon
    normal = render_lark([_one_item_report("t", due=date(2026, 9, 18))], today=TODAY)
    assert "剩 5 天" in normal and "⚠️" not in normal
    none_due = render_lark([_one_item_report("t")], today=TODAY)
    assert "预期" not in none_due


def test_no_open_tasks() -> None:
    """无未完成任务时的输出。"""
    out = render_text([_report()], today=TODAY)
    assert "无未完成任务" in out


def test_done_items_hidden_and_text_has_no_emoji() -> None:
    """已完成条目不展示；终端文本无 emoji、飞书版有。"""
    report = _report(
        sections=[
            Section(
                title="章",
                items=[Item(0, "隐藏我", True), Item(0, "展示我", False)],
            )
        ],
        open_count=1,
        done_count=1,
    )
    text = render_text([report], today=TODAY)
    assert "展示我" in text and "隐藏我" not in text
    assert "📋" not in text
    lark = render_lark([report], today=TODAY)
    assert "📋" in lark


def test_json_structure(tmp_path: Path) -> None:
    """JSON 输出结构完整，供 agent 对接。"""
    report = _one_item_report("任务z", due=date(2026, 9, 18))
    raw = render_json([report], tmp_path, today=TODAY)
    payload = json.loads(raw)
    assert payload["total_open"] == 1
    assert payload["todo_dir"] == str(tmp_path)
    assert payload["generated_at"]
    file = payload["files"][0]
    assert file["title"] == "x-20260901"
    assert file["created"] == "2026-09-01"
    assert file["due"] == "2026-09-18"
    assert file["due_days_left"] == 5
    assert file["sections"][0]["items"][0]["text"] == "任务z"


def test_total_open_sums(tmp_path: Path) -> None:
    """total_open 对多文件求和（走真实 scan 路径）。"""
    (tmp_path / "a-20260901.md").write_text("- [ ] a1\n- [x] a2\n", encoding="utf-8")
    (tmp_path / "b-20260902.md").write_text("- [ ] b1\n", encoding="utf-8")
    assert total_open(scan(tmp_path)) == 2
