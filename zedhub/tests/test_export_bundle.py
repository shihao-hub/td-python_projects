"""批量导出编排测试：文件产出、命名去重、索引与跳过语义。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from zedhub.export.bundle import export_threads


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _field(no: int, payload: bytes) -> bytes:
    return _varint((no << 3) | 2) + _varint(len(payload)) + payload


def _write_transcript(home: Path, sid: str, *, user: str, assistant: str) -> None:
    logs = home / ".gemini" / "antigravity-acp" / "brain" / sid / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    lines = [
        '{"type":"USER_INPUT","content":"<USER_REQUEST>\\n%s\\n</USER_REQUEST>",'
        '"created_at":"2026-10-11T10:00:00Z"}' % user,
        '{"type":"PLANNER_RESPONSE","content":"%s",'
        '"tool_calls":[{"name":"run_command","args":{"CommandLine":"uv run pytest"}}],'
        '"created_at":"2026-10-11T10:01:00Z"}' % assistant,
        '{"type":"RUN_COMMAND","status":"DONE","content":"1 passed",'
        '"created_at":"2026-10-11T10:01:30Z"}',
    ]
    (logs / "transcript.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_step_db(home: Path, sid: str, *, user: str) -> None:
    conv = home / ".gemini" / "antigravity-acp" / "conversations"
    conv.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(conv / f"{sid}.db")
    con.execute(
        "CREATE TABLE steps (idx INTEGER, step_type INTEGER, status TEXT,"
        " metadata BLOB, step_payload BLOB)"
    )
    con.execute(
        "INSERT INTO steps VALUES (0, 14, 'DONE', NULL, ?)",
        (_field(1, user.encode()),),
    )
    con.execute(
        "INSERT INTO steps VALUES (1, 15, 'DONE', NULL, ?)",
        (_field(1, "重建出来的助手回复内容".encode()),),
    )
    con.commit()
    con.close()


def _thread(sid: str, title: str, created: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"tid-{sid}", session_id=sid, agent_id="antigravity-acp", title=title,
        archived=False, projects=["D:\\Users\\language_projects"],
        created_at=created, updated_at=created,
    )


def test_export_writes_md_html_and_index(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_transcript(home, "sid-transcript", user="导出这个会话", assistant="好的，已完成。")
    _write_step_db(home, "sid-db", user="重建会话的用户请求")
    threads = [
        _thread("sid-transcript", "明文会话", datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)),
        _thread("sid-db", "重建会话", datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)),
    ]
    out = tmp_path / "out"
    result = export_threads(threads, output_dir=out, home=home)

    assert result["session_count"] == 2
    assert result["reconstructed_count"] == 1
    assert result["skipped_count"] == 0
    assert (out / "index.md").is_file()
    assert (out / "index.html").is_file()

    files = sorted(p.name for p in (out / "sessions").iterdir())
    assert len(files) == 4
    assert any(name.endswith(".md") for name in files)
    assert any(name.endswith(".html") for name in files)

    index_md = (out / "index.md").read_text(encoding="utf-8")
    assert "明文会话" in index_md and "重建会话" in index_md

    md_path = Path(result["sessions"][0]["md_path"])
    md = md_path.read_text(encoding="utf-8")
    assert md.startswith("---")
    assert "给接手者的快速上下文" in md
    assert "uv run pytest" in md
    assert "1 passed" in md

    html_path = Path(result["sessions"][0]["html_path"])
    page = html_path.read_text(encoding="utf-8")
    assert "<!doctype html>" in page
    assert 'id="q"' in page
    assert "给接手者的快速上下文" in page


def test_export_skips_sessions_without_source(tmp_path: Path) -> None:
    home = tmp_path / "home"
    threads = [_thread("sid-missing", "没有源的会话", datetime(2026, 10, 13, tzinfo=timezone.utc))]
    result = export_threads(threads, output_dir=tmp_path / "out", home=home)
    assert result["session_count"] == 0
    assert result["skipped_count"] == 1
    assert "no transcript" in result["skipped"][0]["reason"] or \
        "无 transcript" in result["skipped"][0]["reason"]


def test_export_dedupes_identical_stems(tmp_path: Path) -> None:
    home = tmp_path / "home"
    stamp = datetime(2026, 10, 14, 9, 30, tzinfo=timezone.utc)
    _write_transcript(home, "sid-a", user="同一个标题", assistant="回复 A")
    _write_transcript(home, "sid-b", user="同一个标题", assistant="回复 B")
    threads = [_thread("sid-a", "同一个标题", stamp), _thread("sid-b", "同一个标题", stamp)]
    out = tmp_path / "out"
    result = export_threads(threads, output_dir=out, home=home)
    assert result["session_count"] == 2
    names = [Path(s["md_path"]).name for s in result["sessions"]]
    assert len(set(names)) == 2


def test_export_detail_minimal_drops_tool_events(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_transcript(home, "sid-m", user="最小档", assistant="答复")
    threads = [_thread("sid-m", "最小档", datetime(2026, 10, 15, tzinfo=timezone.utc))]
    result = export_threads(threads, output_dir=tmp_path / "out", home=home, detail="minimal")
    md = Path(result["sessions"][0]["md_path"]).read_text(encoding="utf-8")
    # minimal 档：正文不保留工具调用/结果块（交接摘要里的命令足迹不受影响）
    assert "工具调用 ·" not in md
    assert "工具结果 ·" not in md
    assert "答复" in md
