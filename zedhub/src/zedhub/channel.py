"""通道（dev/prod）判定与数据目录：core 与壳共用的唯一真源。

- 通道：``ZEDHUB_CHANNEL``（dev|prod）优先，否则由构建形态推导
  （干净发布号 = prod，其余 = dev，见 ``buildid.is_production_build``）；
- 数据目录：``%APPDATA%\\language_projects\\zedhub\\<channel>\\``，取不到
  APPDATA 回退 ``~/.language_projects/zedhub/<channel>/``；
- 一次性迁移：整改前的扁平目录（``.../zedhub/<subdir>``）在首个通道首次
  初始化时只复制 ``index`` 与 ``operations``（旧文件保留不删），marker 写
  旧根 ``.migrated-v1``，全局只迁一次；
- 本模块不 import ``core``：壳（client）可直接复用，避免壳依赖业务层。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .buildid import is_production_build

CHANNEL_ENV = "ZEDHUB_CHANNEL"
CHANNELS = ("dev", "prod")
PROJECT_NAME = "zedhub"

MIGRATION_MARKER = ".migrated-v1"
MIGRATED_SUBDIRS = ("index", "operations")


def _base_dir() -> Path:
    """项目数据根（通道上一层；不创建）。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "language_projects" / PROJECT_NAME
    return Path.home() / ".language_projects" / PROJECT_NAME


def channel() -> str:
    """当前通道：环境变量显式覆盖优先，否则按构建形态推导。"""
    override = (os.environ.get(CHANNEL_ENV) or "").strip().lower()
    if override in CHANNELS:
        return override
    return "prod" if is_production_build() else "dev"


def _legacy_migrated() -> bool:
    return (_base_dir() / MIGRATION_MARKER).exists()


def _migrate_legacy(target: Path) -> None:
    """把扁平目录里可复用的数据复制到首个通道目录（只复制、不删除）。

    只迁 ``index`` 与 ``operations``：``backups`` / ``snapshots`` 体积大且
    可回溯，留在旧根目录；``runtime`` 含陈旧的 daemon 地址/日志，不迁。
    """
    legacy = _base_dir()
    if _legacy_migrated() or not legacy.is_dir() or legacy == target:
        return
    for name in MIGRATED_SUBDIRS:
        src = legacy / name
        dst = target / name
        if not src.is_dir():
            continue
        if dst.exists() and any(dst.iterdir()):
            continue
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        except OSError as exc:
            print(f"zedhub: 迁移旧数据 {name} 失败（继续使用新目录）: {exc}", file=sys.stderr)
    try:
        (legacy / MIGRATION_MARKER).write_text(
            f"migrated_to={target.name}\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"zedhub: 写入迁移标记失败: {exc}", file=sys.stderr)


def data_dir() -> Path:
    """当前通道数据根；返回前确保目录链存在并完成一次性迁移。"""
    target = _base_dir() / channel()
    target.mkdir(parents=True, exist_ok=True)
    _migrate_legacy(target)
    return target


def runtime_dir() -> Path:
    """当前通道的 runtime 子目录（地址文件、拉起日志）；返回前确保存在。"""
    d = data_dir() / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d
