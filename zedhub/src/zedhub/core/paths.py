"""项目数据目录（仓库强约束）。

所有 zedhub 自产运行时数据（快照、备份、操作 journal、daemon 地址文件、
拉起锁、daemon 日志）只允许写入：
- `%APPDATA%\\language_projects\\zedhub\\`（首选）；
- 取不到 APPDATA 时回退 `~/.language_projects/zedhub/`。

禁止因读取外部数据库（Zed/OpenCode）而把自产文件落到其所在目录。
目录链在写入前惰性创建（不调用本模块的纯本地命令零副作用）。
"""

from __future__ import annotations

import os
from pathlib import Path

# 数据目录的一级子目录及用途
SUBDIRS = ("snapshots", "backups", "operations", "runtime")


def zedhub_data_dir() -> Path:
    """项目数据根目录；返回前确保完整目录链存在（含 language_projects 层）。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata) / "language_projects" / "zedhub"
    else:
        base = Path.home() / ".language_projects" / "zedhub"
    base.mkdir(parents=True, exist_ok=True)
    return base


def data_subdir(name: str) -> Path:
    """数据子目录（snapshots/backups/operations/runtime）；返回前确保存在。"""
    if name not in SUBDIRS:
        raise ValueError(f"unknown data subdir: {name!r} (expected one of {SUBDIRS})")
    d = zedhub_data_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d
