"""跨机器归档导入（FR-7，移植 import_sessions.py + 设计 §11 跨库边界）。

一致性边界（不伪造原子性）：
- 不使用 ``ATTACH`` 制造跨 WAL 数据库“整体回滚”的假象；
- 先完成两库 schema/进程/备份预检，再分别执行短事务：先 OpenCode 提交、
  再 Zed 提交、最后分别复查；
- 任一库提交后另一库失败：保留 operation journal、ID 映射、备份和
  ``partial``/``unknown`` 状态，禁止报告成功；本期不自动回滚已提交库。
"""

from __future__ import annotations

import base64
import hashlib
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from .archive import ARCHIVE_SCHEMA_VERSION, ARCHIVE_SOURCE_AGENT
from .backup import backup_database
from .errors import (
    DataSourceMissingError,
    InvalidParamsError,
    SchemaError,
    VerifyFailedError,
)
from .journal import (
    STATUS_OPENCODE_COMMITTED,
    STATUS_PARTIAL,
    STATUS_PLANNED,
    STATUS_UNKNOWN,
    STATUS_VERIFIED,
    STATUS_ZED_COMMITTED,
    OperationRecord,
    save_operation,
)
from .processes import assert_writable, find_running
from .snapshot import default_opencode_db_path, default_zed_db_dir, resolve_db_path
from .writes import (
    checkpoint_wal,
    new_operation_id,
    noop_progress,
    ro_query,
    run_write_transaction,
    ProgressFn,
)

_ZED_INSERT_SQL = (
    "INSERT INTO sidebar_threads"
    " (thread_id, session_id, agent_id, title, title_override, folder_paths,"
    "  folder_paths_order, main_worktree_paths, main_worktree_paths_order,"
    "  archived, created_at, updated_at, interacted_at, remote_connection)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _new_prefixed_id(prefix: str) -> str:
    """OpenCode ID 风格：<prefix>_ + 22 字符 urlsafe base64（uuid 源）。"""
    raw = uuid.uuid4().bytes + uuid.uuid4().bytes[:10]  # 22 bytes
    b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{prefix}_{b64}"


def _load_archive(file: Path) -> dict:
    con = sqlite3.connect(f"file:{file}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        try:
            meta = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM _archive_meta")}
        except sqlite3.DatabaseError as exc:
            raise SchemaError(f"不是可读的 zedhub 归档: {file} ({exc})") from exc
        if meta.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
            raise SchemaError(
                f"归档 schema_version 不识别: {meta.get('schema_version')!r}"
                f"（本版本仅支持 {ARCHIVE_SCHEMA_VERSION}），不猜测旧/新格式"
            )
        if meta.get("source_agent") != ARCHIVE_SOURCE_AGENT:
            raise SchemaError(
                f"归档 source_agent 不识别: {meta.get('source_agent')!r}"
                f"（本版本仅支持 {ARCHIVE_SOURCE_AGENT}）"
            )
        threads = con.execute(
            "SELECT thread_id, session_id, agent_id, title, title_override,"
            "       folder_paths, folder_paths_order, archived, created_at,"
            "       updated_at, interacted_at FROM zed_threads"
        ).fetchall()
        sessions = con.execute(
            "SELECT id, project_id, directory, title, version, agent, model,"
            "       time_created, time_archived FROM opencode_sessions"
        ).fetchall()
        messages = con.execute(
            "SELECT id, session_id, time_created, time_updated, data"
            "  FROM opencode_messages"
        ).fetchall()
        parts = con.execute(
            "SELECT id, message_id, session_id, time_created, time_updated, data"
            "  FROM opencode_parts"
        ).fetchall()
        return {
            "meta": meta, "threads": threads, "sessions": sessions,
            "messages": messages, "parts": parts,
        }
    finally:
        con.close()


def _get_or_create_project(con: sqlite3.Connection, target_dir: str) -> str:
    """获取或创建目标目录的 project 记录（显式列，返回 project_id）。"""
    con.row_factory = sqlite3.Row
    existing = con.execute(
        "SELECT id FROM project WHERE worktree = ?", (target_dir,)
    ).fetchone()
    if existing:
        return existing["id"]
    project_id = hashlib.sha1(
        (str(uuid.uuid4()) + target_dir).encode()
    ).hexdigest()
    now_ms = int(datetime.now().timestamp() * 1000)
    con.execute(
        "INSERT INTO project (id, worktree, vcs, name, time_created, time_updated, sandboxes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (project_id, target_dir, "git", None, now_ms, now_ms, "[]"),
    )
    con.execute(
        "INSERT INTO project_directory (project_id, directory, time_created)"
        " VALUES (?, ?, ?)",
        (project_id, target_dir, now_ms),
    )
    return project_id


def import_archive(
    *,
    file: str,
    target: str,
    apply: bool = False,
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
    progress: ProgressFn = noop_progress,
) -> dict:
    """导入归档到本机 Zed/OpenCode（默认 dry-run，apply=True 才写库）。"""
    f = Path(file)
    if not f.exists():
        raise DataSourceMissingError(f"归档文件不存在: {f}")
    target_dir = str(Path(target))
    if not Path(target_dir).exists():
        raise InvalidParamsError(f"目标目录不存在: {target_dir}（请先创建）")

    progress("validate", "读取归档", 5)
    arc = _load_archive(f)
    n_threads, n_sessions = len(arc["threads"]), len(arc["sessions"])
    n_messages, n_parts = len(arc["messages"]), len(arc["parts"])
    progress("validate", f"thread={n_threads} session={n_sessions} message={n_messages} part={n_parts}", 10)

    zed = resolve_db_path(zed_db, default_zed_db_dir() / "db.sqlite")
    oc = resolve_db_path(opencode_db, default_opencode_db_path(), filename="opencode.db")
    for p, name in ((zed, "Zed"), (oc, "OpenCode")):
        if not p.exists():
            raise DataSourceMissingError(f"{name} 数据库不存在: {p}")

    operation = OperationRecord(
        operation_id=new_operation_id(),
        kind="archive.import",
        params={"file": str(f), "target": target_dir, "apply": apply},
        counts={"threads": n_threads, "sessions": n_sessions,
                "messages": n_messages, "parts": n_parts},
    )
    save_operation(operation)

    def plan_payload(status: str, note: str | None = None) -> dict:
        out = {
            "applied": False,
            "operation_id": operation.operation_id,
            "status": status,
            "file": str(f),
            "target": target_dir,
            "plan": {"threads": n_threads, "sessions": n_sessions,
                     "messages": n_messages, "parts": n_parts},
            "meta": {k: arc["meta"].get(k) for k in
                     ("export_time", "source_project", "include_archived")},
            "backup_dirs": operation.backup_dirs,
        }
        if note:
            out["note"] = note
        return out

    if not apply:
        progress("validate", "dry-run 完成", 100)
        return plan_payload(STATUS_PLANNED, "dry-run 完成；关闭 Zed 与 opencode 后加 --apply 执行实际导入")

    # -- apply 前置检查 -------------------------------------------------------
    running = find_running()
    assert_writable(running)
    progress("process_check", "未检测到 opencode/zed 进程", 15)

    zed_backup = backup_database(zed, operation.operation_id)
    oc_backup = backup_database(oc, operation.operation_id)
    operation.backup_dirs.extend([str(oc_backup), str(zed_backup)])
    save_operation(operation)
    progress("backup", f"备份: {oc_backup.name} / {zed_backup.name}", 20)

    # -- ID 映射（保存进 journal 供恢复使用） ----------------------------------
    session_map = {s["id"]: _new_prefixed_id("ses") for s in arc["sessions"]}
    message_map = {m["id"]: _new_prefixed_id("msg") for m in arc["messages"]}
    part_rows = []
    for p in arc["parts"]:
        new_pid = _new_prefixed_id("prt")
        part_rows.append((new_pid, p["message_id"], p["session_id"], p))
    operation.id_maps = {"session": session_map, "message": message_map}
    operation.counts["part_new_ids"] = len(part_rows)
    save_operation(operation)
    progress("id_map", f"{len(session_map)} session / {len(message_map)} message / {len(part_rows)} part", 30)

    # -- 阶段 1：写 OpenCode（短事务 → checkpoint → 复查基线） ------------------
    def _opencode_stage() -> None:
        con = sqlite3.connect(oc, timeout=15.0)
        try:
            project_id = _get_or_create_project(con, target_dir)
            operation.params["project_id"] = project_id
            rows_session = []
            for s in arc["sessions"]:
                old_sid = s["id"]
                rows_session.append((
                    session_map[old_sid], project_id, None, None,
                    old_sid.split("_")[1][:12] if "_" in old_sid else old_sid[:12],
                    target_dir, "", s["title"], s["version"], None,
                    0, 0, 0, None, None, 0.0, 0, 0, 0, 0, 0, None, None,
                    s["agent"], s["model"], s["time_created"], s["time_created"],
                    None, s["time_archived"],
                ))
            con.executemany(
                "INSERT INTO session (id, project_id, workspace_id, parent_id, slug,"
                " directory, path, title, version, share_url, summary_additions,"
                " summary_deletions, summary_files, summary_diffs, metadata, cost,"
                " tokens_input, tokens_output, tokens_reasoning, tokens_cache_read,"
                " tokens_cache_write, revert, permission, agent, model, time_created,"
                " time_updated, time_compacting, time_archived)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows_session,
            )
            rows_msg = [
                (message_map[m["id"]], session_map.get(m["session_id"]),
                 m["time_created"], m["time_updated"], m["data"])
                for m in arc["messages"] if m["session_id"] in session_map
            ]
            con.executemany(
                "INSERT INTO message (id, session_id, time_created, time_updated, data)"
                " VALUES (?,?,?,?,?)",
                rows_msg,
            )
            rows_part = [
                (pid, message_map[old_mid], session_map[old_sid],
                 p["time_created"], p["time_updated"], p["data"])
                for pid, old_mid, old_sid, p in part_rows
                if old_mid in message_map and old_sid in session_map
            ]
            con.executemany(
                "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data)"
                " VALUES (?,?,?,?,?,?)",
                rows_part,
            )
            con.commit()
        except sqlite3.DatabaseError as exc:
            con.rollback()
            operation.error = f"opencode 阶段失败: {exc}"
            save_operation(operation)
            raise VerifyFailedError(
                f"OpenCode 写入失败已回滚: {exc} [operation={operation.operation_id}]"
            ) from exc
        finally:
            con.close()

    _opencode_stage()
    operation.set_status(STATUS_OPENCODE_COMMITTED)
    save_operation(operation)
    checkpoint_wal(oc, operation_id=operation.operation_id)
    progress("write", f"OpenCode 提交: session={n_sessions} message={n_messages} part={n_parts}", 55)

    # -- 阶段 2：写 Zed（新 thread_id，folder_paths 指向目标目录） ---------------
    zed_rows = [
        (
            uuid.uuid4().bytes, session_map[t["session_id"]], t["agent_id"],
            t["title"], t["title_override"], target_dir, "0", target_dir, "0",
            t["archived"], t["created_at"], t["updated_at"], t["interacted_at"], None,
        )
        for t in arc["threads"] if t["session_id"] in session_map
    ]
    try:
        run_write_transaction(
            zed, [(_ZED_INSERT_SQL, zed_rows)],
            operation=operation, stage="zed_committed", progress=progress,
        )
    except Exception as exc:
        # OpenCode 已提交、Zed 失败：明确 partial，保留恢复证据
        operation.set_status(STATUS_PARTIAL)
        operation.error = f"Zed 阶段失败（OpenCode 已提交）: {exc}"
        save_operation(operation)
        raise VerifyFailedError(
            f"导入部分完成：OpenCode 已提交、Zed 失败。结果状态部分未知，"
            f"请检查备份目录恢复。operation={operation.operation_id}, "
            f"backup={operation.backup_dirs}"
        ) from exc
    checkpoint_wal(zed, operation_id=operation.operation_id)
    progress("write", f"Zed 提交: thread={len(zed_rows)}", 80)

    # -- 阶段 3：分别复查（按目标 directory 计数） -------------------------------
    try:
        oc_count = ro_query(
            oc, "SELECT count(*) c FROM session WHERE directory = ?", (target_dir,)
        )[0][0]
        zed_count = ro_query(
            zed, "SELECT count(*) c FROM sidebar_threads WHERE folder_paths = ?",
            (target_dir,),
        )[0][0]
    except Exception as exc:
        operation.set_status(STATUS_UNKNOWN)
        operation.error = f"复查查询失败（数据可能已写入）: {exc}"
        save_operation(operation)
        raise VerifyFailedError(
            f"导入后复查查询失败，结果未知。operation={operation.operation_id}, "
            f"backup={operation.backup_dirs}"
        ) from exc

    if zed_count < len(zed_rows) or oc_count < n_sessions:
        operation.set_status(STATUS_PARTIAL)
        operation.error = (
            f"复查数量不足: zed {zed_count}/{len(zed_rows)}, "
            f"opencode {oc_count}/{n_sessions}（目标目录可能已有历史数据，请核对）"
        )
        save_operation(operation)
        raise VerifyFailedError(
            f"导入后复查与计划不一致（{operation.error}），结果状态未知，"
            f"请检查备份。operation={operation.operation_id}"
        )

    operation.set_status(STATUS_VERIFIED)
    operation.counts["written"] = {
        "threads": len(zed_rows), "sessions": n_sessions,
        "messages": n_messages, "parts": n_parts,
    }
    save_operation(operation)
    progress("verify", f"复查通过: zed={zed_count} opencode={oc_count}", 100)
    return {
        "applied": True,
        "operation_id": operation.operation_id,
        "status": STATUS_VERIFIED,
        "file": str(f),
        "target": target_dir,
        "written": operation.counts["written"],
        "verify": {"zed_threads": zed_count, "opencode_sessions": oc_count},
        "backup_dirs": operation.backup_dirs,
    }
