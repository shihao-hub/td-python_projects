"""写操作公共基础设施：operation_id、WAL checkpoint、只读复查（设计 §11）。

写流水线由各业务模块（linking/archive/migration）编排；本模块只提供
无业务语义的工具。公共写入核心不调用 os.Exit / 不操作标准流。
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import WriteFailedError
from .journal import OperationRecord, save_operation

# 进度回调：stage 名、明细、可选百分比（SSE 适配层消费）
ProgressFn = Callable[[str, str, int | None], None]


def noop_progress(stage: str, detail: str, pct: int | None = None) -> None:
    """默认进度回调：丢弃（同步 JSON 路径没有消费者）。"""


def new_operation_id() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{uuid.uuid4().hex[:8]}"


def checkpoint_wal(db_file: Path, *, operation_id: str | None = None) -> None:
    """独立新连接执行 PRAGMA wal_checkpoint(TRUNCATE)。

    必须在确认写连接已关闭的前提下调用（WAL 落盘要求）；失败抛
    WriteFailedError（不把 checkpoint 失败伪装成成功）。
    """
    con = sqlite3.connect(db_file, timeout=10.0)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        con.commit()
    except sqlite3.DatabaseError as exc:
        raise WriteFailedError(
            f"WAL checkpoint 失败: {db_file} ({exc})"
            + (f" [operation={operation_id}]" if operation_id else "")
        ) from exc
    finally:
        con.close()


def ro_query(db_file: Path, sql: str, params: tuple = ()) -> Any:
    """重开只读连接复查（写后验证的统一入口）。"""
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=10.0)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def run_write_transaction(
    db_file: Path,
    statements: list[tuple[str, tuple | list]],
    *,
    operation: OperationRecord,
    stage: str,
    progress: ProgressFn = noop_progress,
) -> None:
    """单库短事务：批量参数化语句，commit 后更新 journal 阶段。

    任一语句失败即回滚并抛 WriteFailedError（不虚报成功）。
    """
    con = sqlite3.connect(db_file, timeout=15.0)
    try:
        progress(stage, f"写入 {len(statements)} 条到 {db_file.name}", None)
        for sql, params in statements:
            if isinstance(params, list):
                con.executemany(sql, params)
            else:
                con.execute(sql, params)
        con.commit()
    except sqlite3.DatabaseError as exc:
        con.rollback()
        operation.error = f"{stage} 事务失败: {exc}"
        save_operation(operation)
        raise WriteFailedError(
            f"{stage} 写入失败已回滚: {exc} [operation={operation.operation_id}]"
        ) from exc
    finally:
        con.close()
    operation.set_status(stage)
    save_operation(operation)
