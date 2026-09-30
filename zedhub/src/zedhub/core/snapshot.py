"""安全读库：WAL 三件套快照与只读连接策略（NFR-1）。

Zed 数据库运行于 WAL 模式：最新数据通常在 `-wal` sidecar 中，且 Zed 可能
正在写入。唯一零风险读法是把三件套（主库 + -wal + -shm）一起复制到项目
数据目录再打开副本——Zed 读取始终走快照（设计 §3）。

OpenCode（任务 3）默认 `mode=ro` + busy_timeout 直连，失败才快照兜底，
避免每次复制 GB 级库文件；禁止 `immutable=1`（可能忽略 WAL）。

快照副本只写入 `zedhub_data_dir()/snapshots/<uuid>/`，绝不写入源库目录；
由一次业务请求显式拥有并在 `finally` 中删除。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import DataSourceMissingError, SnapshotError
from .paths import data_subdir


def default_zed_db_dir() -> Path:
    """Zed 数据库目录：ZED_DB_DIR env → %LOCALAPPDATA%\\Zed\\db\\0-stable。"""
    override = os.environ.get("ZED_DB_DIR")
    if override:
        return Path(override)
    localappdata = os.environ.get("LOCALAPPDATA")
    if not localappdata:
        raise SnapshotError("LOCALAPPDATA is not set; pass the Zed db path explicitly.")
    return Path(localappdata) / "Zed" / "db" / "0-stable"


def default_opencode_db_path() -> Path:
    """OpenCode 数据库路径：OPENCODE_DATA env → ~/.local/share/opencode/opencode.db。"""
    override = os.environ.get("OPENCODE_DATA")
    if override:
        return Path(override) / "opencode.db"
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def copy_trio(db_file: Path, dest_dir: Path) -> Path:
    """复制主库 + -wal + -shm 三件套到 dest_dir，返回快照主库路径。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / db_file.name
    try:
        shutil.copy2(db_file, dest)
        for suffix in ("-wal", "-shm"):
            side = db_file.parent / (db_file.name + suffix)
            if side.exists():
                shutil.copy2(side, dest_dir / side.name)
    except OSError as exc:
        raise SnapshotError(f"复制数据库快照失败: {db_file} ({exc})") from exc
    return dest


def resolve_db_path(db: str | Path | None, default: Path, *, filename: str = "db.sqlite") -> Path:
    """把「目录或文件」的入参归一为数据库文件路径。"""
    target = Path(db) if db else default
    if target.is_dir():
        target = target / filename
    return target


@contextmanager
def open_snapshot(db: str | Path | None = None) -> Generator[Path]:
    """Zed 快照读取：三件套复制到数据目录 snapshots/<uuid>/，退出即删。"""
    target = resolve_db_path(db, default_zed_db_dir() / "db.sqlite")
    if not target.exists():
        raise SnapshotError(f"Zed 数据库不存在: {target}")
    snap_dir = data_subdir("snapshots") / str(uuid.uuid4())
    try:
        yield copy_trio(target, snap_dir)
    finally:
        shutil.rmtree(snap_dir, ignore_errors=True)


# -- OpenCode 读取策略（设计 §3） ----------------------------------------------
# 默认 mode=ro + busy_timeout 直连（不复制 GB 级库文件）；连接/读取失败才
# 复制三件套快照重试。绝不使用 immutable=1（可能忽略 WAL 中未 checkpoint 数据）。


def _ro_uri(path: Path) -> str:
    # Windows 下必须 file:///D:/... 三斜杠形式；两斜杠会被当作 authority 解析
    return f"{path.resolve().as_uri()}?mode=ro"


@dataclass
class OpenedDb:
    """open_opencode_ro 的产物：连接 + 来源信息（analytics 报告用）。"""

    con: sqlite3.Connection
    db_path: Path          # 原始库路径（非快照路径）
    using_snapshot: bool


@contextmanager
def open_opencode_ro(db: str | Path | None = None) -> Generator[OpenedDb]:
    """OpenCode 只读连接：ro 直连优先，失败时快照兜底（请求级拥有并关闭）。"""
    target = resolve_db_path(db, default_opencode_db_path(), filename="opencode.db")
    if not target.exists():
        raise DataSourceMissingError(f"OpenCode 数据库不存在: {target}")

    con: sqlite3.Connection | None = None
    snap_dir: Path | None = None
    using_snapshot = False
    try:
        try:
            con = _connect_ro(target)
        except sqlite3.DatabaseError:
            # WAL 无 -shm 窗口期等打开失败：复制三件套后重试，不打扰运行中的 opencode
            snap_dir = data_subdir("snapshots") / str(uuid.uuid4())
            snap = copy_trio(target, snap_dir)
            con = _connect_ro(snap)
            using_snapshot = True
        yield OpenedDb(con=con, db_path=target, using_snapshot=using_snapshot)
    finally:
        if con is not None:
            con.close()
        if snap_dir is not None:
            shutil.rmtree(snap_dir, ignore_errors=True)


def _connect_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(_ro_uri(path), uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    # 立即探测可读性：WAL 窗口期的错误在首次查询才暴露，这里主动探一次
    con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return con
