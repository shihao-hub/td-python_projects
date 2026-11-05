"""Antigravity protobuf 会话重建器单元测试。

覆盖：wire-format 文本扫描、噪声过滤、steps 表 → TimelineEvent 分类。
测试全部自造 protobuf 字节，不依赖本机真实会话数据。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from zedhub.core.agy_proto_text import (
    iter_wire_strings,
    load_agy_events,
    parse_agy_step_db,
)


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
    """构造一个 length-delimited 字段（wire type 2）。"""
    return _varint((no << 3) | 2) + _varint(len(payload)) + payload


def _make_db(path: Path, rows: list[tuple[int, int, bytes]]) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE steps (idx INTEGER, step_type INTEGER, status TEXT,"
        " metadata BLOB, step_payload BLOB)"
    )
    con.executemany(
        "INSERT INTO steps (idx, step_type, status, metadata, step_payload)"
        " VALUES (?, ?, 'DONE', NULL, ?)",
        rows,
    )
    con.commit()
    con.close()


def test_iter_wire_strings_nested() -> None:
    """顶层与嵌套字段里的文本都能提取。"""
    inner = _field(1, "嵌套里的助手回复内容".encode())
    blob = _field(1, "顶层用户请求正文".encode()) + _field(2, inner)
    texts = iter_wire_strings(blob)
    assert "顶层用户请求正文" in texts
    assert "嵌套里的助手回复内容" in texts


def test_iter_wire_strings_ignores_binary() -> None:
    """二进制块不产出可读文本，且不抛异常。"""
    blob = _field(1, bytes(range(32))) + _field(2, "可读文本片段".encode())
    texts = iter_wire_strings(blob)
    assert "可读文本片段" in texts
    assert all("\x00" not in t for t in texts)


def test_parse_steps_user_and_assistant(tmp_path: Path) -> None:
    """step_type 14 → user，15 → assistant。"""
    db = tmp_path / "s.db"
    _make_db(db, [
        (0, 14, _field(1, "帮我写一个函数".encode())),
        (1, 15, _field(1, "好的，我来帮你写一个函数。".encode())),
    ])
    events = parse_agy_step_db(db)
    assert [e.role for e in events] == ["user", "assistant"]
    assert "帮我写一个函数" in events[0].text
    assert "我来帮你写" in events[1].text


def test_parse_steps_tool_call_and_result(tmp_path: Path) -> None:
    """参数 JSON → tool_call；Tool response 回声 → tool_result。"""
    args = b'{"CommandLine":"uv run pytest","toolAction":"Running tests"}'
    db = tmp_path / "s.db"
    _make_db(db, [
        (0, 15, _field(1, args)),
        (1, 21, _field(1, b"Tool response for run_command:\nall tests passed".ljust(40, b" "))),
    ])
    events = parse_agy_step_db(db)
    assert events[0].role == "tool_call"
    assert "uv run pytest" in events[0].text
    assert events[1].role == "tool_result"
    assert events[1].name == "run_command"


def test_parse_steps_filters_uuid_noise(tmp_path: Path) -> None:
    """纯 UUID 拼接块不作为正文。"""
    uuid = "660d23e3-9c0f-4b87-a251-a1994a8b4f5e"
    db = tmp_path / "s.db"
    _make_db(db, [
        (0, 14, _field(1, f'${uuid}"${uuid}'.encode()) + _field(2, "真正的用户请求内容".encode())),
    ])
    events = parse_agy_step_db(db)
    assert events and events[0].role == "user"
    assert "真正的用户请求" in events[0].text
    assert uuid not in events[0].text


def test_load_agy_events_prefers_transcript(tmp_path: Path, monkeypatch) -> None:
    """transcript.jsonl 存在时走无损解析（reconstructed=False）。"""
    home = tmp_path / "home"
    brain = home / ".gemini" / "antigravity-acp" / "brain" / "sid-1" / ".system_generated" / "logs"
    brain.mkdir(parents=True)
    (brain / "transcript.jsonl").write_text(
        '{"type":"USER_INPUT","content":"<USER_REQUEST>\\n无损请求\\n</USER_REQUEST>"}\n',
        encoding="utf-8",
    )
    events, reconstructed = load_agy_events("sid-1", home=home)
    assert reconstructed is False
    assert events and "无损请求" in events[0].text


def test_load_agy_events_falls_back_to_db(tmp_path: Path, monkeypatch) -> None:
    """无 transcript 时回退 steps.db，并标记 reconstructed=True。"""
    home = tmp_path / "home"
    conv = home / ".gemini" / "antigravity-acp" / "conversations"
    conv.mkdir(parents=True)
    _make_db(conv / "sid-2.db", [(0, 14, _field(1, "重建出来的用户输入".encode()))])
    events, reconstructed = load_agy_events("sid-2", home=home)
    assert reconstructed is True
    assert events and "重建出来的用户输入" in events[0].text


def test_load_agy_events_missing_returns_empty(tmp_path: Path) -> None:
    events, reconstructed = load_agy_events("nope", home=tmp_path)
    assert events == []
    assert reconstructed is False
