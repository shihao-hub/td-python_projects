"""Thin read-only SQL layer over a snapshot of Zed's database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

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


class SchemaError(RuntimeError):
    """Zed's schema changed (version migration?); refusing to guess."""


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
        """sidebar_threads stays in the hundreds-to-low-thousands range even
        for heavy use, so loading it whole and filtering in Python keeps the
        query layer trivial and every filter unit-testable without SQL."""
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
