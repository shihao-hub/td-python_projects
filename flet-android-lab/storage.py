import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)

DB_PATH: Path | None = None

_DEFAULT_DIR = Path(__file__).parent / "data"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS day_entries (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    date       TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


def init(db_path: Path) -> None:
    global DB_PATH
    DB_PATH = Path(db_path)
    conn = _connect()
    conn.close()
    logger.info("db_initialized", path=str(DB_PATH))


def _target_path() -> Path:
    return DB_PATH or Path(os.getenv("DAY_ENTRIES_DB") or _DEFAULT_DIR / "day_entries.db")


def _connect() -> sqlite3.Connection:
    path = _target_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(_SCHEMA)
        conn.commit()
    except sqlite3.DatabaseError:
        conn.close()
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        corrupt_path = path.with_name(f"{path.name}.corrupt-{stamp}")
        path.rename(corrupt_path)
        logger.error("db_corrupt_recovered", renamed=str(corrupt_path))
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute(_SCHEMA)
        conn.commit()
    return conn


def list_all() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, name, date, created_at FROM day_entries"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create(name: str, date_iso: str) -> dict:
    item = {
        "id": uuid4().hex,
        "name": name,
        "date": date_iso,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO day_entries (id, name, date, created_at) VALUES (?, ?, ?, ?)",
            (item["id"], item["name"], item["date"], item["created_at"]),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("entry_created", id=item["id"], name=name, date=date_iso)
    return item


def update(item_id: str, name: str, date_iso: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE day_entries SET name = ?, date = ? WHERE id = ?",
            (name, date_iso, item_id),
        )
        conn.commit()
        changed = cur.rowcount > 0
        if changed:
            logger.info("entry_updated", id=item_id, name=name, date=date_iso)
        return changed
    finally:
        conn.close()


def delete(item_id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM day_entries WHERE id = ?", (item_id,))
        conn.commit()
        changed = cur.rowcount > 0
        if changed:
            logger.info("entry_deleted", id=item_id)
        return changed
    finally:
        conn.close()
