"""项目数据目录（仓库强约束 + dev/prod 通道隔离）。

所有 zedhub 自产运行时数据（快照、备份、操作 journal、daemon 地址文件、
拉起日志）只允许写入当前通道目录：
- `%APPDATA%\\language_projects\\zedhub\\{dev,prod}\\`（首选）；
- 取不到 APPDATA 时回退 `~/.language_projects/zedhub/{dev,prod}/`。

通道判定、目录链创建与一次性旧数据迁移统一由 ``zedhub.channel`` 负责；
禁止因读取外部数据库（Zed/OpenCode）而把自产文件落到其所在目录。目录链
在写入前惰性创建（纯本地命令零副作用）。
"""

from __future__ import annotations

from pathlib import Path

from ..channel import data_dir

# 数据目录的一级子目录及用途
SUBDIRS = ("snapshots", "backups", "operations", "runtime", "index")


def zedhub_data_dir() -> Path:
    """当前通道数据根目录；返回前确保完整目录链存在（含 language_projects 层）。"""
    return data_dir()


def data_subdir(name: str) -> Path:
    """数据子目录（snapshots/backups/operations/runtime/index）；返回前确保存在。"""
    if name not in SUBDIRS:
        raise ValueError(f"unknown data subdir: {name!r} (expected one of {SUBDIRS})")
    d = zedhub_data_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def index_db_path() -> Path:
    """会话正文 FTS5 索引库文件绝对路径。"""
    return data_subdir("index") / "content_search.db"


def metadata_cache_db_path() -> Path:
    """会话与线程元数据增量缓存库绝对路径。"""
    return data_subdir("index") / "metadata_cache.db"
