"""trajectory 回归测试：codex content=null 与 opencode 取数分支。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from zedhub.core.trajectory import load_trajectory, parse_codex_file, parse_pi_file


def test_parse_codex_null_content_does_not_crash(tmp_path: Path) -> None:
    """codex jsonl 存在 "content": null：message/reasoning 都不应抛 TypeError。"""
    f = tmp_path / "rollout.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"cwd": "C:\\proj"}, "timestamp": "t0"},
        {"type": "response_item",
         "payload": {"type": "message", "role": "assistant", "content": None},
         "timestamp": "t1"},
        {"type": "response_item",
         "payload": {"type": "reasoning", "content": None},
         "timestamp": "t2"},
        {"type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "hi"}]},
         "timestamp": "t3"},
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    events = parse_codex_file(f)

    assert [e.role for e in events] == ["system", "user"]
    assert events[1].text == "hi"


def _make_opencode_db(tmp_path: Path) -> Path:
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT, directory TEXT,"
        " title TEXT, version TEXT, agent TEXT, model TEXT, parent_id TEXT,"
        " time_created INTEGER, time_updated INTEGER, time_archived INTEGER)"
    )
    con.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,"
        " time_created INTEGER, data TEXT)"
    )
    con.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,"
        " time_created INTEGER, data TEXT)"
    )
    con.execute(
        "INSERT INTO session (id, project_id, directory, title, version, agent, model,"
        " parent_id, time_created, time_updated, time_archived)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("ses_traj_test", "proj", "C:\\proj", "t", "1", "opencode", None, None,
         1700000000000, 1700000001000, None),
    )
    con.execute(
        "INSERT INTO message (id, session_id, time_created, data) VALUES (?,?,?,?)",
        ("msg_u", "ses_traj_test", 1700000000000, json.dumps({"role": "user"})),
    )
    con.execute(
        "INSERT INTO message (id, session_id, time_created, data) VALUES (?,?,?,?)",
        ("msg_a", "ses_traj_test", 1700000001000, json.dumps({"role": "assistant"})),
    )
    con.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, data)"
        " VALUES (?,?,?,?,?)",
        ("part_u", "msg_u", "ses_traj_test", 1700000000000,
         json.dumps({"type": "text", "text": "hello"})),
    )
    con.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, data)"
        " VALUES (?,?,?,?,?)",
        ("part_a", "msg_a", "ses_traj_test", 1700000001000,
         json.dumps({"type": "text", "text": "world"})),
    )
    con.commit()
    con.close()
    return db


def test_load_trajectory_opencode_uses_given_db(tmp_path: Path) -> None:
    """opencode 分支走 get_session_content，且 db 参数生效（默认库无此 sid）。"""
    db = _make_opencode_db(tmp_path)

    out = load_trajectory(source="opencode", files=["ses_traj_test"], db=db)

    assert out["source"] == "opencode"
    assert out["file"] == "opencode:ses_traj_test"
    assert [(e["role"], e["text"]) for e in out["events"]] == [
        ("user", "hello"),
        ("assistant", "world"),
    ]


def test_parse_pi_file_and_load_trajectory(tmp_path: Path) -> None:
    """Pi jsonl 轨迹解析测试：涵盖 session、model_change、thinking、toolCall、toolResult 等事件。"""
    f = tmp_path / "pi_session.jsonl"
    rows = [
        {"type": "session", "cwd": "D:\\my_project", "timestamp": "2026-03-31T01:00:00.000Z"},
        {"type": "model_change", "provider": "deepseek", "modelId": "deepseek-chat", "timestamp": "2026-03-31T01:00:01.000Z"},
        {"type": "thinking_level_change", "thinkingLevel": "high", "timestamp": "2026-03-31T01:00:02.000Z"},
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "text", "text": "你好，帮我看看代码"}],
            "timestamp": "2026-03-31T01:00:03.000Z",
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "用户想要查看代码，我先检索一下。"},
                {"type": "toolCall", "name": "view_file", "arguments": {"path": "main.py"}},
                {"type": "text", "text": "正在为你查看 main.py"},
            ],
            "timestamp": "2026-03-31T01:00:05.000Z",
        },
        {
            "type": "message",
            "role": "toolResult",
            "toolName": "view_file",
            "content": [{"type": "text", "text": "print('hello world')"}],
            "timestamp": "2026-03-31T01:00:06.000Z",
        },
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    # 1. 验证直接 parse_pi_file
    events = parse_pi_file(f)
    assert len(events) == 8
    assert [e.role for e in events] == [
        "system", "system", "system", "user", "thinking", "tool_call", "assistant", "tool_result"
    ]
    assert events[0].text == "cwd: D:\\my_project"
    assert events[1].text == "model: deepseek/deepseek-chat"
    assert events[2].text == "thinking_level: high"
    assert events[3].text == "你好，帮我看看代码"
    assert events[4].text == "用户想要查看代码，我先检索一下。"
    assert events[5].name == "view_file"
    assert json.loads(events[5].text) == {"path": "main.py"}
    assert events[6].text == "正在为你查看 main.py"
    assert events[7].name == "view_file"
    assert events[7].text == "print('hello world')"

    # 2. 验证 load_trajectory 分派
    out = load_trajectory(source="pi", files=[f])
    assert out["source"] == "pi"
    assert out["file"] == str(f)
    assert out["count"] == 8
    assert len(out["events"]) == 8


def test_parse_pi_nested_message_structure(tmp_path: Path) -> None:
    """真实 Pi 格式（内层嵌套 message 对象）解析验证。"""
    f = tmp_path / "pi_nested.jsonl"
    rows = [
        {"type": "session", "cwd": "C:\\repo", "timestamp": "2026-03-31T02:00:00.000Z"},
        {
            "type": "message",
            "timestamp": "2026-03-31T02:00:01.000Z",
            "message": {"role": "system", "content": "", "sections": {"preamble": "You are Pi."}},
        },
        {
            "type": "message",
            "timestamp": "2026-03-31T02:00:02.000Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "ping"}]},
        },
        {
            "type": "message",
            "timestamp": "2026-03-31T02:00:03.000Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "pong"}],
            },
        },
        {
            "type": "message",
            "timestamp": "2026-03-31T02:00:04.000Z",
            "message": {
                "role": "toolResult",
                "toolName": "bash",
                "content": [{"type": "text", "text": "exit 0"}],
            },
        },
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    events = parse_pi_file(f)
    assert [e.role for e in events] == ["system", "system", "user", "assistant", "tool_result"]
    assert events[1].text == "preamble: You are Pi."
    assert events[2].text == "ping"
    assert events[3].text == "pong"
    assert events[4].name == "bash"
    assert events[4].text == "exit 0"


