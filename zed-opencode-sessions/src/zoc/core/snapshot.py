"""Safe read access to Zed's SQLite database.

Zed runs the database in WAL mode: the newest data typically lives in the
``db.sqlite-wal`` sidecar, not in the main file, and Zed may be running (and
writing) while we want to read. The only zero-risk read strategy is to copy
the whole trio (db + -wal + -shm) to a temp directory and open the copy.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

TRIO = ("db.sqlite", "db.sqlite-wal", "db.sqlite-shm")


class SnapshotError(RuntimeError):
    """Raised when the source database cannot be found or copied."""


def default_zed_db_dir() -> Path:
    """获取 Zed 数据库目录（默认位置）"""
    override = os.environ.get("ZED_DB_DIR")
    if override:
        return Path(override)
    localappdata = os.environ.get("LOCALAPPDATA")
    if not localappdata:
        raise SnapshotError("LOCALAPPDATA is not set; pass --db explicitly.")
    return Path(localappdata) / "Zed" / "db" / "0-stable"


def default_opencode_db_path() -> Path:
    """获取 OpenCode 数据库路径（默认位置）"""
    override = os.environ.get("OPENCODE_DATA")
    if override:
        return Path(override) / "opencode.db"
    home = Path.home()
    return home / ".local" / "share" / "opencode" / "opencode.db"


@contextmanager
def open_snapshot(db: str | Path | None = None) -> Generator[Path]:
    """Copy the WAL trio to a temp dir and yield the snapshot db path.

    ``db`` may point at either the db directory (``.../0-stable``) or the
    ``db.sqlite`` file itself. The snapshot is deleted on exit; for a
    one-shot CLI process this keeps things clean and repeatable.
    """
    target = Path(db) if db else default_zed_db_dir() / "db.sqlite"
    if target.is_dir():
        target = target / "db.sqlite"
    if not target.exists():
        raise SnapshotError(f"Database not found: {target}")

    src_dir = target.parent
    snap_dir = Path(tempfile.mkdtemp(prefix="zoc-snapshot-"))
    try:
        shutil.copy2(target, snap_dir / "db.sqlite")
        for suffix in ("-wal", "-shm"):
            side = src_dir / (target.name + suffix)
            if side.exists():
                shutil.copy2(side, snap_dir / (target.name + suffix))
        yield snap_dir / "db.sqlite"
    except OSError as exc:
        raise SnapshotError(f"Failed to snapshot {target}: {exc}") from exc
    finally:
        shutil.rmtree(snap_dir, ignore_errors=True)
