"""Zed SQLite 数据库安全读取（三 agent 共用的 Zed 层）。"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


def get_zed_threads(agent_id: str) -> dict[str, dict[str, Any]]:
    """安全快照读取 Zed sidebar_threads 中指定 agent 的线程元数据。

    快照复制 db/-wal/-shm 三件套避免与运行中的 Zed 锁冲突。
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return {}

    db_dir = Path(local_app_data) / "Zed" / "db" / "0-stable"
    db_file = db_dir / "db.sqlite"
    if not db_file.exists():
        return {}

    temp_dir = tempfile.mkdtemp(prefix="zed_db_read_")
    try:
        # 安全读取：复制 SQLite 三件套 (主库, wal, shm) 避免锁冲突
        for suffix in ["", "-wal", "-shm"]:
            src = db_dir / f"db.sqlite{suffix}"
            if src.exists():
                shutil.copy2(src, Path(temp_dir) / f"db.sqlite{suffix}")

        temp_db = Path(temp_dir) / "db.sqlite"
        with contextlib.closing(sqlite3.connect(temp_db)) as connection:
            rows = connection.execute(
                """
                SELECT session_id, title, folder_paths, created_at, updated_at
                FROM sidebar_threads
                WHERE agent_id = ?
                ORDER BY updated_at DESC
                """,
                (agent_id,),
            ).fetchall()
        results = {}
        for row in rows:
            sid, title, folder_paths, created_at, updated_at = row
            paths = [p for p in (folder_paths or "").split("\n") if p.strip()]
            results[str(sid)] = {
                "title": title or "",
                "folder_paths": paths,
                "created_at": created_at or "",
                "updated_at": updated_at or "",
            }
        return results
    except Exception:
        return {}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
