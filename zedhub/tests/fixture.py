"""Build a synthetic sidebar_threads database mirroring Zed's real schema."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

COLUMNS = """
thread_id BLOB NOT NULL PRIMARY KEY,
session_id TEXT,
agent_id TEXT,
title TEXT,
title_override TEXT,
updated_at TEXT,
created_at TEXT,
folder_paths TEXT,
folder_paths_order TEXT,
archived INTEGER DEFAULT 0,
main_worktree_paths TEXT,
main_worktree_paths_order TEXT,
remote_connection TEXT,
interacted_at TEXT,
title_override2 TEXT
"""


def make_db(tmp_path: Path) -> Path:
    con = sqlite3.connect(tmp_path / "db.sqlite")
    con.execute(f"CREATE TABLE sidebar_threads ({COLUMNS})")
    con.close()
    return tmp_path / "db.sqlite"


def insert(con: sqlite3.Connection, *, agent="opencode", title="hello",
           override=None, paths="C:\\proj\\alpha", archived=0,
           updated="2026-09-01T09:30:29.605964800+00:00",
           created="2026-08-27T01:00:00.000000+00:00", session="ses_x") -> str:
    tid = uuid.uuid4()
    con.execute(
        "INSERT INTO sidebar_threads (thread_id, session_id, agent_id, title, title_override,"
        " updated_at, created_at, folder_paths, folder_paths_order, archived,"
        " main_worktree_paths, main_worktree_paths_order)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid.bytes, session, agent, title, override, updated, created,
         paths, "0" if "\n" not in paths else "0,1", archived, paths,
         "0" if "\n" not in paths else "0,1"),
    )
    return str(tid)
