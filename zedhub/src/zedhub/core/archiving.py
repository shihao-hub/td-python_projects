"""批量归档 / 恢复 Zed 会话（按 agent + 工作区，可逆）。

为什么需要专门模块：Zed 没有批量归档 CLI，手工点 86 条不现实；直接改
``sidebar_threads.archived`` 又必须遵守写库安全流程（进程退出检查、三件套
备份、短事务、WAL checkpoint、只读复查）。

可逆性设计：每次归档都会在 ``<数据根>/backups/<operation_id>/undo-archive.json``
写入**精确的 thread_id 清单**。恢复只回滚这份清单里的会话，不会误伤用户
后来手动归档的其他会话，也不依赖「按 agent 再扫一遍」这种会漂移的语义。

约定：``--apply`` 前必须完全退出 Zed（WAL 写入竞态不可控）；dry-run 只读
快照，可随时查看将影响哪些会话。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .backup import backup_database, backup_dir
from .journal import OperationRecord, save_operation
from .model import Thread
from .paths import data_subdir
from .processes import assert_writable, find_running
from .repo import ZedDb
from .service import ThreadService
from .snapshot import default_zed_db_dir, open_snapshot, resolve_db_path
from .writes import (
    ProgressFn,
    checkpoint_wal,
    new_operation_id,
    noop_progress,
    ro_query,
    run_write_transaction,
)

# undo 清单文件名（与 db 备份同目录，便于整体搬运/排查）
UNDO_FILE = "undo-archive.json"
# 计划/结果里返回的会话明细上限（避免 CLI 输出失控）
_LIST_LIMIT = 300


@dataclass
class ArchivePlan:
    """一次归档 / 恢复的计划（dry-run 与 apply 共用）。"""

    action: str                      # archive | restore
    agent: str
    project: str
    exact: bool
    threads: list[Thread] = field(default_factory=list)
    source_operation: str = ""       # restore：来源归档操作 id
    skipped: int = 0                 # 已在目标状态、无需变更的数量

    @property
    def thread_ids(self) -> list[str]:
        return [t.id for t in self.threads]


def _thread_blob(thread_id: str) -> bytes:
    """uuid 字符串 → Zed 的 16 字节 thread_id BLOB。"""
    try:
        return uuid.UUID(thread_id).bytes
    except ValueError:
        return bytes.fromhex(thread_id)


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def undo_path(operation_id: str) -> Path:
    return backup_dir(operation_id) / UNDO_FILE


def read_undo(operation_id: str) -> dict | None:
    """读指定操作的 undo 清单；不存在返回 None。"""
    path = data_subdir("backups") / operation_id / UNDO_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_undos() -> list[dict]:
    """列出可用于恢复的归档操作（新到旧），附带是否已恢复。"""
    root = data_subdir("backups")
    items: list[dict] = []
    if not root.is_dir():
        return items
    for path in root.glob(f"*/{UNDO_FILE}"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            items.append(data)
    items.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
    return items


def latest_undo(agent: str | None = None, *, unapplied_only: bool = True) -> dict | None:
    """最近一次归档清单（默认只看尚未恢复的）。"""
    for item in list_undos():
        if agent and item.get("agent") != agent:
            continue
        if unapplied_only and item.get("restored_at"):
            continue
        return item
    return None


def plan_archive(
    *,
    agent: str,
    project: str,
    exact: bool = True,
    zed_db: Path | None = None,
) -> ArchivePlan:
    """列出将归档的会话（只读快照，不写库）。"""
    zed = resolve_db_path(zed_db, default_zed_db_dir() / "db.sqlite")
    with open_snapshot(zed) as snap:
        with ZedDb(snap) as db:
            threads = ThreadService(db).list_threads(
                project=project, agent=agent, archived="no", exact=exact,
            )
    threads.sort(key=lambda t: t.updated_at or t.created_at or datetime.min, reverse=True)
    return ArchivePlan(action="archive", agent=agent, project=project,
                       exact=exact, threads=threads)


def plan_restore(
    *,
    operation_id: str | None = None,
    zed_db: Path | None = None,
) -> ArchivePlan:
    """按 undo 清单列出待恢复会话（只读快照）。"""
    undo = read_undo(operation_id) if operation_id else latest_undo()
    if undo is None:
        raise ValueError(
            "没有可恢复的归档记录（可用 zedhub sessions archive --list 查看）"
        )
    if operation_id is None:
        operation_id = str(undo.get("operation_id") or "")
    if undo.get("restored_at"):
        raise ValueError(f"该归档操作已于 {undo['restored_at']} 恢复过；如需重复恢复请显式指定 --operation")

    ids = [str(x) for x in (undo.get("thread_ids") or [])]
    if not ids:
        return ArchivePlan(action="restore", agent=str(undo.get("agent") or ""),
                           project=str(undo.get("project") or ""), exact=bool(undo.get("exact", True)),
                           source_operation=operation_id or "")

    zed = resolve_db_path(zed_db, default_zed_db_dir() / "db.sqlite")
    with open_snapshot(zed) as snap:
        with ZedDb(snap) as db:
            by_id = {t.id: t for t in db.load_threads()}
    wanted: list[Thread] = []
    for tid in ids:
        thread = by_id.get(tid)
        if thread is not None and thread.archived:
            wanted.append(thread)
    return ArchivePlan(
        action="restore", agent=str(undo.get("agent") or ""),
        project=str(undo.get("project") or ""), exact=bool(undo.get("exact", True)),
        threads=wanted, source_operation=operation_id or "",
        skipped=len(ids) - len(wanted),
    )


def _write_undo(operation_id: str, payload: dict) -> Path:
    path = undo_path(operation_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _verify(zed: Path, thread_ids: list[str], *, expect_archived: bool) -> int:
    """只读复查：返回仍处于期望状态的条数。"""
    blobs = [_thread_blob(tid) for tid in thread_ids]
    placeholders = ",".join("?" for _ in blobs)
    rows = ro_query(
        zed,
        f"SELECT thread_id, archived FROM sidebar_threads WHERE thread_id IN ({placeholders})",
        tuple(blobs),
    )
    want = 1 if expect_archived else 0
    return sum(1 for _, archived in rows if int(archived or 0) == want)


def execute_archive(
    plan: ArchivePlan,
    *,
    zed_db: Path | None = None,
    progress: ProgressFn = noop_progress,
) -> dict:
    """apply：进程检查 → 备份 → 事务 → checkpoint → 复查。"""
    if not plan.threads:
        return {
            "action": plan.action, "applied": False, "count": 0,
            "reason": "没有需要变更的会话", "agent": plan.agent,
            "project": plan.project, "threads": [],
        }
    zed = resolve_db_path(zed_db, default_zed_db_dir() / "db.sqlite")
    archiving = plan.action == "archive"
    operation = OperationRecord(
        operation_id=new_operation_id(),
        kind=f"sessions.{plan.action}",
        params={"agent": plan.agent, "project": plan.project,
                "exact": plan.exact, "source_operation": plan.source_operation},
        counts={"planned": len(plan.threads)},
    )
    save_operation(operation)
    progress("validate", f"计划{('归档' if archiving else '恢复')} {len(plan.threads)} 个会话", 5)

    running = find_running()
    assert_writable(running)
    progress("process_check", "未检测到 opencode/zed 进程", 10)

    backup = backup_database(zed, operation.operation_id)
    operation.backup_dirs.append(str(backup))
    save_operation(operation)
    progress("backup", f"Zed 备份: {backup}", 20)

    now = _iso_now()
    if archiving:
        undo = {
            "operation_id": operation.operation_id,
            "created_at": now,
            "action": "archive",
            "agent": plan.agent,
            "project": plan.project,
            "exact": plan.exact,
            "thread_ids": plan.thread_ids,
            "count": len(plan.threads),
            "restored_at": None,
        }
        undo_file = _write_undo(operation.operation_id, undo)
    else:
        undo_file = undo_path(plan.source_operation)
        undo = read_undo(plan.source_operation) or {}
        undo["restored_at"] = now
        undo["restore_operation_id"] = operation.operation_id
        undo_file = _write_undo(plan.source_operation, undo)
    progress("undo", f"undo 清单: {undo_file}", 30)

    target = 1 if archiving else 0
    rows = [("UPDATE sidebar_threads SET archived = ? WHERE thread_id = ?",
             (target, _thread_blob(tid))) for tid in plan.thread_ids]
    run_write_transaction(
        zed, rows, operation=operation, stage="zed_committed", progress=progress,
    )
    progress("write", f"写入 {len(rows)} 条，事务已提交", 70)

    checkpoint_wal(zed, operation_id=operation.operation_id)
    progress("checkpoint", "WAL 已落盘", 85)

    ok = _verify(zed, plan.thread_ids, expect_archived=archiving)
    if ok != len(plan.thread_ids):
        operation.error = f"复查不一致：期望 {len(plan.thread_ids)}，实际 {ok}"
        operation.set_status("partial")
        save_operation(operation)
        raise RuntimeError(
            f"{'归档' if archiving else '恢复'}后复查失败（{ok}/{len(plan.thread_ids)}），"
            f"请用备份回滚。operation={operation.operation_id}"
        )

    operation.counts["written"] = len(rows)
    operation.set_status("verified")
    save_operation(operation)
    progress("verify", f"{len(rows)} 个会话复查通过", 100)
    return {
        "action": plan.action,
        "applied": True,
        "agent": plan.agent,
        "project": plan.project,
        "count": len(rows),
        "skipped": plan.skipped,
        "operation_id": operation.operation_id,
        "source_operation": plan.source_operation,
        "backup_dirs": operation.backup_dirs,
        "undo_file": str(undo_file),
        "threads": [
            {
                "thread_id": t.id,
                "session_id": t.session_id,
                "title": t.title,
                "created_at": t.created_at.isoformat() if t.created_at else "",
            }
            for t in plan.threads[:_LIST_LIMIT]
        ],
        "verify": "ok",
    }


def run_archive(
    *,
    agent: str,
    project: str,
    exact: bool = True,
    restore: bool = False,
    operation_id: str | None = None,
    list_operations: bool = False,
    apply: bool = False,
    zed_db: Path | None = None,
    progress: ProgressFn = noop_progress,
) -> dict:
    """统一入口：plan（dry-run）或 apply。"""
    if list_operations:
        return {"action": "list", "operations": list_undos()}
    plan = (
        plan_restore(operation_id=operation_id, zed_db=zed_db)
        if restore
        else plan_archive(agent=agent, project=project, exact=exact, zed_db=zed_db)
    )
    if not apply:
        return {
            "action": plan.action,
            "applied": False,
            "agent": plan.agent,
            "project": plan.project,
            "count": len(plan.threads),
            "skipped": plan.skipped,
            "source_operation": plan.source_operation,
            "threads": [
                {
                    "thread_id": t.id,
                    "session_id": t.session_id,
                    "title": t.title,
                    "created_at": t.created_at.isoformat() if t.created_at else "",
                }
                for t in plan.threads[:_LIST_LIMIT]
            ],
            "note": "dry-run；关闭 Zed 后加 --apply 执行",
        }
    return execute_archive(plan, zed_db=zed_db, progress=progress)


__all__ = [
    "ArchivePlan", "execute_archive", "latest_undo", "list_undos",
    "plan_archive", "plan_restore", "read_undo", "run_archive", "undo_path",
]
