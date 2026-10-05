"""trajectory 回归测试：codex content=null 与 opencode 取数分支。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from zedhub.core.trajectory import load_trajectory, parse_codex_file


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
