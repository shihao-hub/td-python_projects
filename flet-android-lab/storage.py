import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

DB_PATH: Path | None = None

_DEFAULT_DIR = Path(__file__).parent / "data"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS anniversaries (
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


def _target_path() -> Path:
    return DB_PATH or Path(os.getenv("ANNIVERSARY_DB") or _DEFAULT_DIR / "anniversaries.db")


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
        path.rename(path.with_name(f"{path.name}.corrupt-{stamp}"))
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute(_SCHEMA)
        conn.commit()
    return conn


def list_all() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, name, date, created_at FROM anniversaries"
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
            "INSERT INTO anniversaries (id, name, date, created_at) VALUES (?, ?, ?, ?)",
            (item["id"], item["name"], item["date"], item["created_at"]),
        )
        conn.commit()
    finally:
        conn.close()
    return item


def update(item_id: str, name: str, date_iso: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE anniversaries SET name = ?, date = ? WHERE id = ?",
            (name, date_iso, item_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete(item_id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM anniversaries WHERE id = ?", (item_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
