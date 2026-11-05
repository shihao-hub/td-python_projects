"""导出渲染层测试：Markdown 结构、HTML 安全转义与折叠交互。"""

from __future__ import annotations

from zedhub.core.handoff import build_handoff
from zedhub.core.trajectory import TimelineEvent
from zedhub.export.reader_html import md_to_html, render_html
from zedhub.export.transcript_md import render_front_matter, render_markdown

_META = {
    "title": "导出测试会话", "directory": "D:\\Users\\language_projects",
    "agent": "antigravity-acp", "session_id": "sid-9", "thread_id": "tid-9",
    "created_at": "2026-10-11T10:00:00+08:00", "updated_at": "2026-10-11T11:00:00+08:00",
}


def _events() -> list[TimelineEvent]:
    return [
        TimelineEvent(role="user", text="帮我看看这个脚本", ts="2026-10-11T10:00:00+08:00"),
        TimelineEvent(role="tool_call", name="run_command",
                      text='{"CommandLine": "uv run pytest", "toolAction": "run"}',
                      ts="2026-10-11T10:01:00+08:00"),
        TimelineEvent(role="tool_result", name="run_command", status="DONE",
                      text="1 passed", ts="2026-10-11T10:01:30+08:00"),
        TimelineEvent(role="assistant", text="**完成**：测试通过。", ts="2026-10-11T10:02:00+08:00"),
    ]


def test_front_matter_lists_core_fields() -> None:
    h = build_handoff(_events(), meta=_META)
    fm = render_front_matter(h)
    assert fm.startswith("---")
    assert 'session_id: "sid-9"' in fm
    assert 'reconstructed: "false"' not in fm  # 布尔不加引号
    assert "reconstructed: false" in fm


def test_render_markdown_three_sections() -> None:
    h = build_handoff(_events(), meta=_META)
    md = render_markdown(h, _events(), detail="brief")
    assert md.startswith("---")
    assert "给接手者的快速上下文" in md
    assert "# 对话全文" in md
    assert "🔧 工具调用" in md
    assert "📤 工具结果" in md
    assert "**完成**：测试通过。" in md


def test_render_markdown_minimal_drops_tools() -> None:
    h = build_handoff(_events(), meta=_META)
    md = render_markdown(h, _events(), detail="minimal")
    assert "🔧 工具调用" not in md
    assert "📤 工具结果" not in md
    assert "帮我看看这个脚本" in md


def test_md_to_html_escapes_script() -> None:
    out = md_to_html("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_md_to_html_links() -> None:
    assert '<a href="https://example.com"' in md_to_html("[文档](https://example.com)")
    assert 'href="file:///D:/a.md"' in md_to_html("[本地](file:///D:/a.md)")
    assert "<a href" not in md_to_html("[坏链](javascript:alert(1))")
    assert "坏链" in md_to_html("[坏链](javascript:alert(1))")


def test_render_html_has_search_and_collapsed_tools() -> None:
    h = build_handoff(_events(), meta=_META)
    page = render_html(h, _events())
    assert "<!doctype html>" in page
    assert 'id="q"' in page
    assert "<details class=\"ev ev-tool\"" in page
    assert "导出测试会话" in page
    assert 'id="theme-btn"' in page


def test_render_html_marks_reconstructed() -> None:
    h = build_handoff(_events(), meta=_META, reconstructed=True)
    page = render_html(h, _events())
    assert "启发式重建" in page
