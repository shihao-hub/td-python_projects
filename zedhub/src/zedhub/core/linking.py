"""会话补登到 Zed 索引（FR-6，移植 link_sessions.py；三源扩展）。

- 复用原 session_id（正文关联不断）、uuid4().bytes 作 thread_id；时间统一
  转 Zed UTC ISO（9 位小数 + +00:00）；
- source=opencode：走 OpenCode session 表（毫秒时间戳 → Zed ISO），查重
  语义保持**全局 session_id**（存量行为不动）；
- source=claude-code/codex/antigravity：走 agent_sessions 浅层元数据扫描
  （时间已是 Zed ISO），查重按 ``(agent_id, session_id, 目标目录)`` 三元组
  ——同会话跨目录允许新挂（原目录入口保留，两个工作区都能打开）；
- 目录子串匹配 + ``--all``/``--target`` 消歧（逻辑照搬归档脚本）；
- 幂等：已存在的目标行跳过；写入单事务；复查插入的 (session_id, folder)
  集合与快照重读比对；
- 默认 dry-run；``apply=True`` 走完整流水线：进程检查 → 备份 → 事务 →
  checkpoint → 只读复查 → 报告。
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .agent_paths import FILE_SOURCES, agent_id_for
from .agent_sessions import scan_agent_sessions
from .errors import InvalidParamsError, NotFoundError, VerifyFailedError
from .journal import (
    STATUS_PLANNED,
    STATUS_VERIFIED,
    OperationRecord,
    save_operation,
)
from .processes import assert_writable, find_running
from .backup import backup_database
from .writes import checkpoint_wal, new_operation_id, noop_progress, ro_query, run_write_transaction, ProgressFn

from .repo import ZedDb
from .snapshot import open_opencode_ro, open_snapshot, resolve_db_path, default_zed_db_dir, default_opencode_db_path
from .opencode_repo import OpencodeDb


def normalize_dir(path: str) -> str:
    """归一化目录：统一平台原生格式（Windows 反斜杠）。"""
    return os.path.normpath(path)


def ms_to_zed_ts(ms: int) -> str:
    """OpenCode 毫秒时间戳 → Zed UTC ISO（9 位小数 + +00:00）。"""
    total_ns = int(ms) * 1_000_000
    sec, ns = divmod(total_ns, 1_000_000_000)
    base = datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{ns:09d}+00:00"


@dataclass
class LinkPlan:
    query: str
    source: str = "opencode"                                # opencode | 三文件源
    matched_sessions: list[dict] = field(default_factory=list)
    groups: dict[str, int] = field(default_factory=dict)   # 目录 → 会话数
    link_dir: str | None = None                            # 统一目标目录（--target）
    per_session_dir: bool = False                          # --all：各回各家
    to_insert: list[tuple[dict, str]] = field(default_factory=list)
    skipped_subagents: int = 0
    already_linked: int = 0
    no_directory: int = 0                                   # 三源：无法定位目录被跳过数


def _load_opencode_sessions(opencode_db: Path) -> list[dict]:
    with open_opencode_ro(opencode_db) as od:
        con = od.con
        rows = con.execute(
            "SELECT id, directory, title, time_created, time_updated,"
            "       time_archived, parent_id FROM session"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "directory": normalize_dir(r["directory"] or ""),
                "title": r["title"] or "",
                "time_created": r["time_created"],
                "time_updated": r["time_updated"],
                "time_archived": r["time_archived"],
                "parent_id": r["parent_id"],
            }
            for r in rows
        ]


def _load_agent_sessions(source: str) -> tuple[list[dict], int]:
    """三文件源 → （与 opencode session dict 同形状的列表，无法定位目录数）。

    时间直接是 Zed ISO（扫描层已转换）；directory 为空的记录（如
    antigravity 无 .meta）跳过并计数。
    """
    records = scan_agent_sessions(source)
    no_directory = sum(1 for r in records if not r.directory)
    sessions = [
        {
            "id": r.session_id,
            "directory": r.directory,
            "title": r.title,
            "time_created": r.time_created,
            "time_updated": r.time_updated,
            "time_archived": None,
            "parent_id": None,
        }
        for r in records
        if r.directory
    ]
    return sessions, no_directory


def _zed_linked_ids(zed_db: Path) -> set[str]:
    with open_snapshot(zed_db) as snap:
        with ZedDb(snap) as db:
            return db.linked_session_ids()


def _zed_linked_folders(zed_db: Path, agent_id: str) -> set[tuple[str, str]]:
    """Zed 索引中某 agent 的 ``(session_id, folder)`` 集合（三源查重用）。

    folder_paths 换行分隔多值，逐行展开；两边都 normpath 后比较，避免
    分隔符差异造成重复挂载。
    """
    with open_snapshot(zed_db) as snap:
        con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT session_id, folder_paths FROM sidebar_threads"
                " WHERE agent_id = ? AND session_id IS NOT NULL",
                (agent_id,),
            ).fetchall()
        finally:
            con.close()
    out: set[tuple[str, str]] = set()
    for sid, folders in rows:
        for p in (folders or "").split("\n"):
            p = p.strip()
            if p:
                out.add((sid, normalize_dir(p)))
    return out


def plan_link(
    query: str,
    *,
    source: str = "opencode",
    all_dirs: bool = False,
    target: str | None = None,
    include_subagents: bool = False,
    zed_db: Path,
    opencode_db: Path,
) -> LinkPlan:
    """生成补登计划（纯读，dry-run 与 apply 共用）。"""
    if source == "opencode":
        plan = LinkPlan(query=query, source=source)
        sessions = _load_opencode_sessions(opencode_db)
    elif source in FILE_SOURCES:
        plan = LinkPlan(query=query, source=source)
        sessions, plan.no_directory = _load_agent_sessions(source)
    else:
        raise InvalidParamsError(
            f"source 未支持: {source}；link 已支持: opencode, " + ", ".join(FILE_SOURCES)
        )

    if not include_subagents:
        before = len(sessions)
        sessions = [s for s in sessions if not s["parent_id"]]
        plan.skipped_subagents = before - len(sessions)

    q = normalize_dir(query.strip()).lower()
    matched = [s for s in sessions if q and q in s["directory"].lower()]
    if not matched:
        raise NotFoundError(f"未找到目录匹配 {query!r} 的会话")

    groups: dict[str, list[dict]] = {}
    for s in matched:
        groups.setdefault(s["directory"], []).append(s)
    plan.matched_sessions = matched
    plan.groups = {d: len(items) for d, items in groups.items()}

    forced_target = normalize_dir(target) if target else None
    if len(groups) > 1 and not all_dirs and not forced_target:
        raise InvalidParamsError(
            f"子串匹配到 {len(groups)} 个目录，无法确定目标。请二选一:\n"
            "  1) 传入完整精确目录重跑\n"
            "  2) 加 --all 逐目录处理（会话挂到各自目录）\n"
            "  3) 加 --target <目录> 把所有命中会话挂到指定 Zed 工作区"
        )
    if forced_target:
        plan.link_dir = forced_target
    elif all_dirs:
        plan.per_session_dir = True
    else:
        plan.link_dir = next(iter(groups))

    if source == "opencode":
        # 存量语义：全局 session_id 查重（不动已交付行为的幂等预期）
        linked = _zed_linked_ids(zed_db)
        for s in matched:
            if s["id"] in linked:
                plan.already_linked += 1
                continue
            folder = plan.link_dir if plan.link_dir is not None else s["directory"]
            plan.to_insert.append((s, folder))
    else:
        # 三源语义：(agent_id, session_id, 目标目录) 三元组查重——跨目录允许新挂
        linked = _zed_linked_folders(zed_db, agent_id_for(source))
        for s in matched:
            folder = plan.link_dir if plan.link_dir is not None else s["directory"]
            if (s["id"], normalize_dir(folder)) in linked:
                plan.already_linked += 1
                continue
            plan.to_insert.append((s, folder))
    return plan


_ZED_INSERT_SQL = (
    "INSERT INTO sidebar_threads"
    " (thread_id, session_id, agent_id, title, title_override,"
    "  folder_paths, folder_paths_order, main_worktree_paths,"
    "  main_worktree_paths_order, archived, created_at, updated_at,"
    "  interacted_at, remote_connection)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def execute_link(
    plan: LinkPlan,
    *,
    zed_db: Path,
    opencode_db: Path,
    progress: ProgressFn = noop_progress,
) -> dict:
    """apply 路径：进程检查 → 备份 → 单事务 → checkpoint → 复查。"""
    agent_id = agent_id_for(plan.source)  # opencode → "opencode"；三源 → Zed agent_id
    operation = OperationRecord(
        operation_id=new_operation_id(),
        kind="sessions.link",
        params={"query": plan.query, "target": plan.link_dir,
                "all_dirs": plan.per_session_dir, "source": plan.source},
        counts={"planned": len(plan.to_insert), "already_linked": plan.already_linked},
    )
    save_operation(operation)
    progress("validate", f"计划补登 {len(plan.to_insert)} 个会话", 5)

    running = find_running()
    assert_writable(running)
    progress("process_check", "未检测到 opencode/zed 进程", 10)

    backup = backup_database(zed_db, operation.operation_id)
    operation.backup_dirs.append(str(backup))
    save_operation(operation)
    progress("backup", f"Zed 备份: {backup}", 20)

    def _zed_ts(s: dict, key: str) -> str:
        # opencode：毫秒 int → Zed ISO；三源：扫描层已是 Zed ISO 直用
        if plan.source == "opencode":
            return ms_to_zed_ts(s[key])
        return s[key]

    rows = [
        (
            uuid.uuid4().bytes,
            s["id"],
            agent_id,
            s["title"] or "",
            None,
            folder,
            "0",
            folder,
            "0",
            1 if s["time_archived"] is not None else 0,   # 三源无归档概念，恒 0
            _zed_ts(s, "time_created"),
            _zed_ts(s, "time_updated"),
            _zed_ts(s, "time_updated"),
            None,
        )
        for s, folder in plan.to_insert
    ]
    run_write_transaction(
        zed_db, [(_ZED_INSERT_SQL, rows)],
        operation=operation, stage="zed_committed",
        progress=progress,
    )
    progress("write", f"写入 {len(rows)} 条，事务已提交", 70)

    checkpoint_wal(zed_db, operation_id=operation.operation_id)
    progress("checkpoint", "WAL 已落盘", 85)

    # 复查：重开只读连接核对插入的 (session_id, folder) 集合
    # （三源跨目录挂载时同一 session_id 可能已有原目录行，只查 sid 会误判）
    inserted = {(s["id"], normalize_dir(folder)) for s, folder in plan.to_insert}
    got = {
        (r[0], normalize_dir(p))
        for r in ro_query(
            zed_db,
            "SELECT session_id, folder_paths FROM sidebar_threads WHERE session_id IS NOT NULL",
        )
        for p in (r[1] or "").split("\n")
        if p.strip()
    }
    missing = inserted - got
    if missing:
        operation.error = f"复查缺失 {len(missing)} 个 (session_id, folder)"
        operation.set_status("partial")
        save_operation(operation)
        raise VerifyFailedError(
            f"补登后复查发现 {len(missing)} 个 (session_id, folder) 未被索引，"
            f"结果状态未知，请检查备份。operation={operation.operation_id}, "
            f"missing={sorted(missing)[:5]}"
        )

    operation.counts["written"] = len(rows)
    operation.set_status(STATUS_VERIFIED)
    save_operation(operation)
    progress("verify", f"全部 {len(inserted)} 个 (session_id, folder) 复查通过", 100)
    return _result(operation, plan, applied=True)


def run_link(
    *,
    project: str,
    source: str = "opencode",
    all_dirs: bool = False,
    target: str | None = None,
    include_subagents: bool = False,
    apply: bool = False,
    zed_db: Path | None,
    opencode_db: Path | None,
    progress: ProgressFn = noop_progress,
) -> dict:
    """统一入口：dry-run 输出计划；apply 执行写流水线。"""
    zed = resolve_db_path(zed_db, default_zed_db_dir() / "db.sqlite")
    oc = resolve_db_path(opencode_db, default_opencode_db_path(), filename="opencode.db")

    plan = plan_link(
        project, source=source, all_dirs=all_dirs, target=target,
        include_subagents=include_subagents, zed_db=zed, opencode_db=oc,
    )
    progress("validate", f"命中 {len(plan.matched_sessions)} 个会话，待补登 {len(plan.to_insert)}", 5)
    if not apply or not plan.to_insert:
        return _result(
            _plan_record(plan), plan, applied=False,
            note=None if plan.to_insert else "没有需要补登的会话，无需操作",
        )
    return execute_link(plan, zed_db=zed, opencode_db=oc, progress=progress)


def _plan_record(plan: LinkPlan) -> OperationRecord:
    rec = OperationRecord(
        operation_id=new_operation_id(),
        kind="sessions.link",
        params={"query": plan.query, "dry_run": True, "source": plan.source},
        counts={"planned": len(plan.to_insert), "already_linked": plan.already_linked},
    )
    return rec


def _result(operation: OperationRecord, plan: LinkPlan, *, applied: bool, note: str | None = None) -> dict:
    preview = [
        {"session_id": s["id"], "title": (s["title"] or "(untitled)")[:60], "directory": folder}
        for s, folder in plan.to_insert[:10]
    ]
    out = {
        "applied": applied,
        "operation_id": operation.operation_id,
        "status": operation.status,
        "query": plan.query,
        "source": plan.source,
        "matched": len(plan.matched_sessions),
        "directories": plan.groups,
        "skipped_subagents": plan.skipped_subagents,
        "already_linked": plan.already_linked,
        "no_directory": plan.no_directory,
        "planned": len(plan.to_insert),
        "preview": preview,
        "backup_dirs": operation.backup_dirs,
    }
    if applied:
        out["written"] = operation.counts.get("written", 0)
        out["verify"] = "ok"
    if note:
        out["note"] = note
    return out
