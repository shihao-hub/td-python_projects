"""Zed 索引源只读层（快照之上的显式列 SQL）。

ZedDb 是独立的 Zed 索引源（不伪装成 AgentSource）：负责 thread 查询、
session_id 关联和项目统计的数据读取；schema 不符抛 SchemaError。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .errors import SchemaError  # noqa: F401 — 重导出：归档基线的导入路径
from .model import Thread, blob_to_thread_id, parse_paths, parse_ts

REQUIRED_COLUMNS = {
    "thread_id",
    "session_id",
    "agent_id",
    "title",
    "title_override",
    "updated_at",
    "created_at",
    "folder_paths",
    "archived",
    "interacted_at",
}


class ZedDb:
    def __init__(self, snapshot_path: Path | str) -> None:
        self.path = Path(snapshot_path)
        self.con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        self._check_schema()

    def _check_schema(self) -> None:
        try:
            rows = self.con.execute("PRAGMA table_info(sidebar_threads)").fetchall()
        except sqlite3.DatabaseError as exc:
            raise SchemaError(f"Not a readable Zed database: {self.path} ({exc})") from exc
        cols = {r["name"] for r in rows}
        if not cols:
            raise SchemaError(f"Table sidebar_threads not found in {self.path}")
        missing = REQUIRED_COLUMNS - cols
        if missing:
            raise SchemaError(
                "Zed schema mismatch, missing columns: "
                + ", ".join(sorted(missing))
                + " — a Zed upgrade likely migrated the table; update zedhub."
            )

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "ZedDb":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def load_threads(self) -> list[Thread]:
        """sidebar_threads 常年在数百到数千量级：全量载入后在 Python 侧过滤，
        查询层保持极简、每个筛选条件可脱离 SQL 单测。"""
        rows = self.con.execute(
            "SELECT thread_id, session_id, agent_id, title, title_override,"
            "       updated_at, created_at, folder_paths, archived, interacted_at"
            "  FROM sidebar_threads"
        ).fetchall()
        threads = []
        for r in rows:
            title = r["title_override"] or r["title"] or ""
            threads.append(
                Thread(
                    id=blob_to_thread_id(r["thread_id"]),
                    session_id=r["session_id"],
                    agent_id=r["agent_id"] or "unknown",
                    title=title.strip(),
                    archived=bool(r["archived"]),
                    projects=parse_paths(r["folder_paths"]),
                    created_at=parse_ts(r["created_at"]),
                    updated_at=parse_ts(r["updated_at"]),
                    interacted_at=parse_ts(r["interacted_at"]),
                )
            )
        return threads

    def linked_session_ids(self) -> set[str]:
        """Zed 索引中已存在的 session_id 集合（link 幂等与关联查询用）。"""
        rows = self.con.execute(
            "SELECT session_id FROM sidebar_threads WHERE session_id IS NOT NULL"
        ).fetchall()
        return {r[0] for r in rows}
