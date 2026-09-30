"""写前备份：数据库三件套复制到项目数据目录（FR-6/FR-7/NFR-4）。

备份只写入 ``zedhub_data_dir()/backups/<operation_id>/<db 文件名>/``，
绝不写入外部数据库目录或 %TEMP%（修正归档脚本的 %TEMP% 违规）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .errors import DataDirError
from .paths import data_subdir
from .snapshot import copy_trio


def backup_dir(operation_id: str) -> Path:
    """备份根目录（写前确保可写，不可写即终止）。"""
    d = data_subdir("backups") / operation_id
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".writable"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise DataDirError(f"备份目录不可写: {d} ({exc})") from exc
    return d


def backup_database(db_file: Path, operation_id: str) -> Path:
    """备份单个数据库三件套到 ``backups/<operation_id>/``，返回备份目录。

    zed（db.sqlite）与 opencode（opencode.db）文件名天然不同，同一
    operation 目录下并存不冲突。
    """
    root = backup_dir(operation_id)
    if not db_file.exists():
        raise DataDirError(f"待备份数据库不存在: {db_file}")
    copy_trio(db_file, root)  # 返回值为主库路径；这里统一记目录
    return root


def cleanup_backup_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
