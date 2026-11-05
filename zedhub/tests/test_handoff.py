"""交接摘要（handoff）单元测试：足迹提取、兜底逻辑与 Markdown 卡片。"""

from __future__ import annotations

import json

from zedhub.core.handoff import build_handoff, render_card
from zedhub.core.trajectory import TimelineEvent

_META = {
    "title": "会话标题", "directory": "D:\\Users\\language_projects",
    "agent": "antigravity-acp", "session_id": "sid-1", "thread_id": "tid-1",
    "created_at": "2026-10-11T11:13:29+08:00", "updated_at": "2026-10-11T12:00:00+08:00",
}


def _tool(name: str, args: dict) -> TimelineEvent:
    return TimelineEvent(role="tool_call", name=name,
                         text=json.dumps(args, ensure_ascii=False), ts="2026-10-11T11:14:00+08:00")


def test_build_handoff_collects_footprints() -> None:
    events = [
        TimelineEvent(role="user", text="帮我改一下导出"),
        _tool("view_file", {"AbsolutePath": "D:\\p\\a.py", "toolAction": "Reading"}),
        _tool("client_create_file", {"target_file": "D:\\p\\b.md", "code_content": "x"}),
        _tool("run_command", {"CommandLine": "uv run pytest -q", "Cwd": "D:\\p"}),
        TimelineEvent(role="assistant", text="已经改好了。"),
    ]
    h = build_handoff(events, meta=_META)
    assert h.files == ["D:\\p\\a.py", "D:\\p\\b.md"]
    assert h.commands == ["uv run pytest -q"]
    assert h.tool_counts == {"view_file": 1, "client_create_file": 1, "run_command": 1}
    assert h.last_assistant == "已经改好了。"
    assert h.user_requests == ["帮我改一下导出"]


def test_build_handoff_ignores_urls_and_dedupes() -> None:
    events = [
        _tool("read_url_content", {"Url": "https://example.com/docs"}),
        _tool("view_file", {"AbsolutePath": "D:\\p\\a.py"}),
        _tool("view_file", {"AbsolutePath": "D:\\p\\a.py"}),
    ]
    h = build_handoff(events, meta=_META)
    assert h.files == ["D:\\p\\a.py"]


def test_build_handoff_falls_back_to_title_and_directory() -> None:
    events = [_tool("run_command", {"CommandLine": "ls", "Cwd": "."})]
    h = build_handoff(events, meta={"session_id": "sid-2"})
    assert h.title == ""
    assert h.directory in ("", ".")  # "." 是否存在的目录由环境决定，不强制

    events2 = [TimelineEvent(role="user", text="这是第一条用户请求，应该变成标题")]
    h2 = build_handoff(events2, meta={"session_id": "sid-3"})
    assert h2.title.startswith("这是第一条用户请求")


def test_build_handoff_keeps_checkpoint() -> None:
    checkpoint = "# Resuming from a compaction\n\n# User Requests\n- 做导出"
    events = [
        TimelineEvent(role="user", text="开始"),
        TimelineEvent(role="system", name="checkpoint", text=checkpoint),
    ]
    h = build_handoff(events, meta=_META, reconstructed=True)
    assert h.progress_summary.startswith("# Resuming from a compaction")
    assert h.reconstructed is True


def test_render_card_marks_reconstructed_and_lists_items() -> None:
    events = [
        TimelineEvent(role="user", text="任务目标"),
        _tool("run_command", {"CommandLine": "pytest"}),
        TimelineEvent(role="assistant", text="结论：通过。"),
        TimelineEvent(role="system", text="# Resuming from a compaction\n摘要内容"),
    ]
    h = build_handoff(events, meta=_META, reconstructed=True)
    card = render_card(h)
    assert "启发式重建" in card
    assert "任务目标" in card
    assert "`pytest`" in card
    assert "结论：通过。" in card
    assert "agy 进度摘要" in card
