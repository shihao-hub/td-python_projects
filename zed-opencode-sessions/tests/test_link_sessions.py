"""link_sessions 脚本的单元测试（合成数据库，不触碰真实库）。"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"

# 以文件路径加载 scripts/link_sessions.py（scripts 不是包）
_spec = importlib.util.spec_from_file_location("link_sessions", SCRIPTS_DIR / "link_sessions.py")
link_sessions = importlib.util.module_from_spec(_spec)
sys.modules["link_sessions"] = link_sessions
_spec.loader.exec_module(link_sessions)  # type: ignore[union-attr]


ZED_DDL = """
CREATE TABLE sidebar_threads (
  thread_id BLOB PRIMARY KEY,
  session_id TEXT,
  agent_id TEXT,
  title TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  created_at TEXT,
  folder_paths TEXT,
  folder_paths_order TEXT,
  archived INTEGER DEFAULT 0,
  main_worktree_paths TEXT,
  main_worktree_paths_order TEXT,
  remote_connection TEXT,
  interacted_at TEXT,
  title_override TEXT
) STRICT;
"""

OC_DDL = """
CREATE TABLE session (
  id TEXT PRIMARY KEY,
  directory TEXT NOT NULL,
  title TEXT NOT NULL,
  time_created INTEGER NOT NULL,
  time_updated INTEGER NOT NULL,
  time_archived INTEGER,
  parent_id TEXT
);
"""


def _make_opencode_db(path: Path, sessions: list[dict]) -> None:
    con = sqlite3.connect(path)
    con.executescript(OC_DDL)
    con.executemany(
        "INSERT INTO session (id, directory, title, time_created, time_updated, time_archived, parent_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                s["id"],
                s["directory"],
                s.get("title", "t"),
                s.get("time_created", 1_700_000_000_000),
                s.get("time_updated", 1_700_000_001_000),
                s.get("time_archived"),
                s.get("parent_id"),
            )
            for s in sessions
        ],
    )
    con.commit()
    con.close()


def _make_zed_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(ZED_DDL)
    con.commit()
    con.close()


def test_normalize_dir_unifies_slashes():
    assert link_sessions.normalize_dir("D:/Users/x") == link_sessions.normalize_dir("D:\\Users\\x")


def test_ms_to_zed_ts_format():
    assert link_sessions.ms_to_zed_ts(1_789_232_467_185) == "2026-09-12T17:01:07.185000000+00:00"


def test_apply_links_and_is_idempotent(tmp_path, monkeypatch):
    oc = tmp_path / "opencode.db"
    zed = tmp_path / "db.sqlite"
    ms = 1_700_000_000_000

    _make_opencode_db(
        oc,
        [
            # 正斜杠目录，应归一化为反斜杠
            {"id": "ses_a", "directory": "D:/proj", "title": "会话A", "time_created": ms, "time_updated": ms + 5000},
            # subagent，默认跳过
            {"id": "ses_b", "directory": "D:/proj", "title": "子会话", "parent_id": "ses_a"},
            # 其他目录，不应命中
            {"id": "ses_c", "directory": "D:/other", "title": "别的"},
        ],
    )
    _make_zed_db(zed)

    monkeypatch.setattr(link_sessions, "check_processes", lambda names: [])

    link_sessions.link_sessions(
        query="proj", apply=True, zed_db=zed, opencode_db=oc, include_subagents=False
    )

    con = sqlite3.connect(zed)
    rows = con.execute(
        "SELECT session_id, agent_id, title, folder_paths, folder_paths_order,"
        " main_worktree_paths, archived, created_at, updated_at, interacted_at"
        " FROM sidebar_threads"
    ).fetchall()
    con.close()

    assert len(rows) == 1
    sid, agent, title, folder, order, wt, archived, created, updated, interacted = rows[0]
    assert sid == "ses_a"
    assert agent == "opencode"
    assert title == "会话A"
    assert folder == "D:\\proj"
    assert order == "0"
    assert wt == "D:\\proj"
    assert archived == 0
    assert created == "2023-11-14T22:13:20.000000000+00:00"
    assert updated == "2023-11-14T22:13:25.000000000+00:00"
    assert interacted == updated

    # 幂等：再跑一次不应重复插入
    link_sessions.link_sessions(query="proj", apply=True, zed_db=zed, opencode_db=oc)
    con = sqlite3.connect(zed)
    count = con.execute("SELECT count(*) FROM sidebar_threads").fetchone()[0]
    con.close()
    assert count == 1


def test_multi_dir_requires_disambiguation(tmp_path, monkeypatch, capsys):
    oc = tmp_path / "opencode.db"
    zed = tmp_path / "db.sqlite"
    _make_opencode_db(
        oc,
        [
            {"id": "ses_a", "directory": "D:/p1"},
            {"id": "ses_b", "directory": "D:/p2"},
        ],
    )
    _make_zed_db(zed)

    with pytest.raises(SystemExit) as exc:
        link_sessions.link_sessions(query=":", zed_db=zed, opencode_db=oc)
    assert exc.value.code == 1


def test_all_flag_links_each_to_own_dir(tmp_path, monkeypatch):
    oc = tmp_path / "opencode.db"
    zed = tmp_path / "db.sqlite"
    _make_opencode_db(
        oc,
        [
            {"id": "ses_a", "directory": "D:/p1"},
            {"id": "ses_b", "directory": "D:/p2"},
        ],
    )
    _make_zed_db(zed)
    monkeypatch.setattr(link_sessions, "check_processes", lambda names: [])

    link_sessions.link_sessions(query="D:/p", all_dirs=True, apply=True, zed_db=zed, opencode_db=oc)

    con = sqlite3.connect(zed)
    pairs = dict(con.execute("SELECT session_id, folder_paths FROM sidebar_threads").fetchall())
    con.close()
    assert pairs == {"ses_a": "D:\\p1", "ses_b": "D:\\p2"}


def test_target_overrides_folder(tmp_path, monkeypatch):
    oc = tmp_path / "opencode.db"
    zed = tmp_path / "db.sqlite"
    _make_opencode_db(oc, [{"id": "ses_a", "directory": "D:/p1/sub"}])
    _make_zed_db(zed)
    monkeypatch.setattr(link_sessions, "check_processes", lambda names: [])

    link_sessions.link_sessions(
        query="D:/p1", target="D:/workspace", apply=True, zed_db=zed, opencode_db=oc
    )

    con = sqlite3.connect(zed)
    folder = con.execute("SELECT folder_paths FROM sidebar_threads").fetchone()[0]
    con.close()
    assert folder == "D:\\workspace"
