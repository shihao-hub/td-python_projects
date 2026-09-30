"""归档导出与检查（FR-5，移植 export_sessions.py）。

- 归档 schema = 原 5 表结构 + 索引；所有查询显式列（禁 ``SELECT *``，
  修正归档脚本违规）；
- ``_archive_meta`` 固定键含 ``schema_version=1`` 与 ``source_agent``；
- 输出路径由用户显式指定（``-o``），不写内部数据目录。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .errors import DataSourceMissingError, InvalidParamsError, NotFoundError, SchemaError
from .repo import ZedDb
from .service import ThreadService
from .snapshot import (
    default_opencode_db_path,
    default_zed_db_dir,
    open_opencode_ro,
    open_snapshot,
    resolve_db_path,
)
from .writes import noop_progress, ProgressFn

ARCHIVE_SCHEMA_VERSION = "1"
ARCHIVE_SOURCE_AGENT = "opencode"

ARCHIVE_SCHEMA = """
CREATE TABLE _archive_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE zed_threads (
    thread_id BLOB PRIMARY KEY, session_id TEXT, agent_id TEXT, title TEXT,
    title_override TEXT, folder_paths TEXT, folder_paths_order TEXT,
    archived INTEGER, created_at TEXT, updated_at TEXT, interacted_at TEXT
);
CREATE TABLE opencode_sessions (
    id TEXT PRIMARY KEY, project_id TEXT, directory TEXT, title TEXT,
    version TEXT, agent TEXT, model TEXT, time_created INTEGER,
    time_archived INTEGER
);
CREATE TABLE opencode_messages (
    id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
    time_updated INTEGER, data TEXT
);
CREATE TABLE opencode_parts (
    id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
    time_created INTEGER, time_updated INTEGER, data TEXT
);
CREATE INDEX idx_zed_threads_session ON zed_threads(session_id);
CREATE INDEX idx_messages_session ON opencode_messages(session_id);
CREATE INDEX idx_parts_session ON opencode_parts(session_id);
CREATE INDEX idx_parts_message ON opencode_parts(message_id);
"""


def export_archive(
    *,
    project: str,
    output: str,
    include_archived: bool = False,
    exact: bool = False,
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
    progress: ProgressFn = noop_progress,
) -> dict:
    """导出指定项目范围的 Zed/OpenCode 会话为可迁移 SQLite 归档。"""
    import os

    zed = resolve_db_path(zed_db, default_zed_db_dir() / "db.sqlite")
    oc = resolve_db_path(opencode_db, default_opencode_db_path(), filename="opencode.db")
    out = Path(output)
    if out.parent and not out.parent.exists():
        raise InvalidParamsError(f"输出目录不存在: {out.parent}")

    # 用户习惯正斜杠，Zed folder_paths 是平台原生分隔符：归一化后匹配
    project_q = os.path.normpath(project)

    progress("validate", f"查询 Zed threads (project={project})", 5)
    with open_snapshot(zed) as snap:
        with ZedDb(snap) as db:
            threads = ThreadService(db).list_threads(
                project=project_q, agent="opencode",
                archived="all" if include_archived else "no",
            )
            if exact:
                # --exact：归一化后与 folder path 完全相等才命中，不带出子目录
                want = project_q.casefold()
                threads = [t for t in threads if any(os.path.normpath(p).casefold() == want for p in t.projects)]
    if not threads:
        raise NotFoundError(f"未找到匹配的会话: project={project!r}")
    session_ids = [t.session_id for t in threads if t.session_id]
    if not session_ids:
        raise NotFoundError("所有匹配 thread 都没有 session_id，无可导出内容")
    progress("validate", f"{len(threads)} 个 thread，{len(session_ids)} 个有 session_id", 15)

    placeholders = ",".join("?" * len(session_ids))
    with open_opencode_ro(oc) as od:
        con = od.con
        sessions = con.execute(
            f"SELECT id, project_id, directory, title, version, agent, model,"
            f"       time_created, time_archived FROM session WHERE id IN ({placeholders})",
            session_ids,
        ).fetchall()
        if not sessions:
            raise NotFoundError("OpenCode 中未找到对应 session 记录")
        progress("read", f"读取 {len(sessions)} 个 session", 35)
        messages = con.execute(
            f"SELECT id, session_id, time_created, time_updated, data"
            f"  FROM message WHERE session_id IN ({placeholders})",
            session_ids,
        ).fetchall()
        progress("read", f"读取 {len(messages)} 条 message", 55)
        parts = con.execute(
            f"SELECT id, message_id, session_id, time_created, time_updated, data"
            f"  FROM part WHERE session_id IN ({placeholders})",
            session_ids,
        ).fetchall()
        progress("read", f"读取 {len(parts)} 个 part", 70)
        # Zed 原始行（含 BLOB thread_id 与全列，显式列）
        with open_snapshot(zed) as snap:
            zcon = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
            zcon.row_factory = sqlite3.Row
            try:
                zed_rows = zcon.execute(
                    f"SELECT thread_id, session_id, agent_id, title, title_override,"
                    f"       folder_paths, folder_paths_order, archived, created_at,"
                    f"       updated_at, interacted_at"
                    f"  FROM sidebar_threads WHERE session_id IN ({placeholders})",
                    session_ids,
                ).fetchall()
            finally:
                zcon.close()

    progress("write", f"写归档 {out}", 85)
    if out.exists():
        out.unlink()
    arc = sqlite3.connect(out)
    try:
        arc.executescript(ARCHIVE_SCHEMA)
        arc.executemany(
            "INSERT INTO _archive_meta (key, value) VALUES (?, ?)",
            [
                ("schema_version", ARCHIVE_SCHEMA_VERSION),
                ("source_agent", ARCHIVE_SOURCE_AGENT),
                ("export_time", datetime.now(timezone.utc).isoformat(timespec="seconds")),
                ("source_project", project),
                ("thread_count", str(len(zed_rows))),
                ("session_count", str(len(sessions))),
                ("message_count", str(len(messages))),
                ("part_count", str(len(parts))),
                ("include_archived", str(include_archived)),
                ("match_mode", "exact" if exact else "substring"),
            ],
        )
        arc.executemany(
            "INSERT INTO zed_threads VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [tuple(r) for r in zed_rows],
        )
        arc.executemany(
            "INSERT INTO opencode_sessions VALUES (?,?,?,?,?,?,?,?,?)",
            [tuple(r) for r in sessions],
        )
        arc.executemany(
            "INSERT INTO opencode_messages VALUES (?,?,?,?,?)",
            [tuple(r) for r in messages],
        )
        arc.executemany(
            "INSERT INTO opencode_parts VALUES (?,?,?,?,?,?)",
            [tuple(r) for r in parts],
        )
        arc.commit()
    finally:
        arc.close()

    result = {
        "output": str(out),
        "size_bytes": out.stat().st_size,
        "thread_count": len(zed_rows),
        "session_count": len(sessions),
        "message_count": len(messages),
        "part_count": len(parts),
        "include_archived": include_archived,
        "match_mode": "exact" if exact else "substring",
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "source_agent": ARCHIVE_SOURCE_AGENT,
    }
    progress("verify", "归档已写入并提交", 100)
    return result


def read_archive_meta(file: str) -> dict:
    """读取归档元数据与各表计数（inspect 与 import 前置校验共用）。"""
    f = Path(file)
    if not f.exists():
        raise DataSourceMissingError(f"归档文件不存在: {f}")
    con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        try:
            meta = {
                r["key"]: r["value"]
                for r in con.execute("SELECT key, value FROM _archive_meta")
            }
        except sqlite3.DatabaseError as exc:
            raise SchemaError(f"不是可读的 zedhub 归档: {f} ({exc})") from exc
        if not meta:
            raise SchemaError(f"归档缺少 _archive_meta: {f}")
        counts = {}
        for table in ("zed_threads", "opencode_sessions", "opencode_messages", "opencode_parts"):
            try:
                counts[table] = con.execute(f"SELECT count(*) c FROM {table}").fetchone()["c"]
            except sqlite3.DatabaseError:
                counts[table] = None
        return {"meta": meta, "counts": counts, "file": str(f)}
    finally:
        con.close()


def inspect_archive(file: str) -> dict:
    """archive inspect 命令实现：元数据 + 计数投影。"""
    info = read_archive_meta(file)
    meta = info["meta"]
    return {
        "file": info["file"],
        "schema_version": meta.get("schema_version"),
        "source_agent": meta.get("source_agent"),
        "export_time": meta.get("export_time"),
        "source_project": meta.get("source_project"),
        "include_archived": meta.get("include_archived") == "True",
        "match_mode": meta.get("match_mode", "substring"),
        "counts": info["counts"],
    }
