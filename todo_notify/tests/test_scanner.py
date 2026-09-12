"""scanner 解析规则测试：代码块陷阱 / 缩进子项 / [x] 大小写 / 非 md 忽略 / 元信息。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from todo_notify.scanner import scan

# 覆盖全部解析规则的样例文档
SAMPLE = """# 主题A 待办

| 项目 | 内容 |
| --- | --- |
| 创建时间 | 2026-09-01 |
| 预期完成 | 2026-09-20 |

## 一、章节甲

- [ ] 顶层任务一
- [x] 已完成顶层任务
  - [ ] 子任务（缩进 2 空格）
- [X] 大写 X 也算已完成

~~~yaml
# 围栏内的复选框不得计入
- [ ] 围栏内未完成
- [x] 围栏内已完成
~~~

```sql
-- 反引号围栏内的复选框也不计入
- [ ] SQL 示例任务
```

## 二、章节乙

- [ ] 顶层任务二
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_code_fences_skipped(tmp_path: Path) -> None:
    """``` 与 ~~~ 围栏内的复选框一律不计入。"""
    _write(tmp_path, "主题A-20260901.md", SAMPLE)
    reports = scan(tmp_path)
    assert len(reports) == 1
    texts = [i.text for s in reports[0].sections for i in s.items]
    assert "围栏内未完成" not in texts
    assert "SQL 示例任务" not in texts
    assert reports[0].open_count == 3  # 顶层任务一、子任务、顶层任务二
    assert reports[0].done_count == 2  # [x] 与 [X]


def test_section_and_indent(tmp_path: Path) -> None:
    """复选框归属最近前置标题，缩进空格数保留。"""
    _write(tmp_path, "主题A-20260901.md", SAMPLE)
    report = scan(tmp_path)[0]
    by_title = {s.title: s for s in report.sections}
    sec_a = by_title["一、章节甲"]
    assert [i.text for i in sec_a.items] == [
        "顶层任务一",
        "已完成顶层任务",
        "子任务（缩进 2 空格）",
        "大写 X 也算已完成",
    ]
    assert [i.indent for i in sec_a.items] == [0, 0, 2, 0]
    assert by_title["二、章节乙"].items[0].done is False


def test_checkbox_case_insensitive_done(tmp_path: Path) -> None:
    """[x] 与 [X] 均视为已完成。"""
    _write(tmp_path, "主题A-20260901.md", SAMPLE)
    report = scan(tmp_path)[0]
    done_items = [i for s in report.sections for i in s.items if i.done]
    assert [i.text for i in done_items] == ["已完成顶层任务", "大写 X 也算已完成"]


def test_non_markdown_ignored(tmp_path: Path) -> None:
    """非 .md 文件不扫描，子目录递归扫描。"""
    _write(tmp_path, "主题A-20260901.md", SAMPLE)
    _write(tmp_path, "notes.txt", "- [ ] 文本文件里的复选框")
    _write(tmp_path / "sub", "子目录-20260902.md", "- [ ] 子目录任务")
    reports = scan(tmp_path)
    assert sorted(r.title for r in reports) == ["主题A-20260901", "子目录-20260902"]


def test_metadata_best_effort(tmp_path: Path) -> None:
    """文件名尾部日期 → created；元信息表「预期完成」→ due。"""
    _write(tmp_path, "主题A-20260901.md", SAMPLE)
    report = scan(tmp_path)[0]
    assert report.created == date(2026, 9, 1)
    assert report.due == date(2026, 9, 20)
    assert report.title == "主题A-20260901"


def test_metadata_missing_or_invalid(tmp_path: Path) -> None:
    """缺文件名日期、缺元信息、非法日期均不影响扫描。"""
    _write(tmp_path, "无日期.md", "- [ ] 任务")
    _write(tmp_path, "非法日期-20269999.md", "- [ ] 任务")
    _write(tmp_path, "日期ok-20260901.md", "| 预期完成 | 2026-13-99 |\n\n- [ ] 任务")
    reports = {r.title: r for r in scan(tmp_path)}
    assert reports["无日期"].created is None
    assert reports["无日期"].due is None
    assert reports["无日期"].open_count == 1
    assert reports["非法日期-20269999"].created is None
    assert reports["日期ok-20260901"].due is None


def test_items_before_any_heading(tmp_path: Path) -> None:
    """文件开头无标题时复选框落入空标题章节。"""
    _write(tmp_path, "主题B-20260903.md", "- [ ] 无标题先行任务\n\n## 后来标题\n\n- [ ] 标题下任务")
    report = scan(tmp_path)[0]
    assert report.sections[0].title == ""
    assert report.sections[0].items[0].text == "无标题先行任务"
