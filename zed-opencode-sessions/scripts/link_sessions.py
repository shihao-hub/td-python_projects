#!/usr/bin/env python
"""
将某目录下已存在的 opencode 会话补登到 Zed（sidebar_threads），使其在 Zed 历史列表可见。

背景:
    直接用 opencode CLI 创建的会话只存在 opencode.db 中，Zed 从未为其建立
    sidebar_threads 索引，因此在该目录打开 Zed 时 agent 历史列表为空。
    本脚本把缺失的会话“补登”进 Zed，复用 opencode 原有 session_id，正文关联不断。

    只写 Zed 数据库，不修改 opencode.db；幂等（已存在的会话自动跳过）。

用法:
    # 默认 dry-run 预览（子串匹配，多个目录时会中止并要求消歧）
    uv run python scripts/link_sessions.py language_projects

    # 传精确目录（消歧后重跑）
    uv run python scripts/link_sessions.py "D:/Users/language_projects"

    # 子串命中多个目录时全部处理（每个会话挂到自己所属目录）
    uv run python scripts/link_sessions.py language_projects --all

    # 强制把命中会话全部挂到指定 Zed 工作区
    uv run python scripts/link_sessions.py language_projects --target "D:/Users/language_projects"

    # 实际写入（默认只预览）。写入前请完全退出 Zed 和 opencode
    uv run python scripts/link_sessions.py "D:/Users/language_projects" --apply

设计:
    - 默认 dry-run，只有 --apply 才写库
    - 检查 opencode/Zed 进程，要求手动关闭（写库与一致读取都需要）
    - 自动备份 Zed 数据库三件套到 %TEMP%\\zoc_backup
    - 路径归一化：opencode 目录用正斜杠，Zed folder_paths 用反斜杠，统一 os.path.normpath
    - 时间格式：opencode 毫秒时间戳 -> Zed 的 UTC ISO（9 位小数 + +00:00）
    - 默认跳过 parent_id 非空的子会话（Zed 不为 subagent 建独立 thread）
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Windows 控制台 UTF-8 输出（避免 emoji/中文乱码）
# 用 reconfigure 而非替换 sys.stdout，避免被测试工具 import 时破坏其捕获流
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

# 添加项目路径到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from zoc.core.snapshot import (
    TRIO,
    default_opencode_db_path,
    default_zed_db_dir,
    open_snapshot,
)


# ========== 通用辅助 ==========

def normalize_dir(path: str) -> str:
    """归一化目录：opencode 用正斜杠、Zed 用反斜杠，统一成平台原生格式（Windows 反斜杠）。"""
    return os.path.normpath(path)


def ms_to_zed_ts(ms: int) -> str:
    """opencode 毫秒时间戳 -> Zed UTC ISO 字符串（9 位小数 + +00:00）。

    例: 1789232467185 -> 2026-09-12T17:01:07.185000000+00:00
    """
    total_ns = int(ms) * 1_000_000
    sec, ns = divmod(total_ns, 1_000_000_000)
    base = datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{ns:09d}+00:00"


def check_processes(process_names: list[str]) -> list[str]:
    """检查指定进程是否在运行（Windows）。"""
    if sys.platform != "win32":
        return []

    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
        ).stdout

        running = []
        for line in out.splitlines():
            first = line.split('","', 1)[0].strip('"').lower()
            for name in process_names:
                if name.lower() in first:
                    running.append(first)
                    break

        return running
    except Exception:
        return []


def backup_database(db_file: Path, backup_root: Path) -> Path:
    """备份数据库三件套。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = backup_root / f"backup_{db_file.stem}_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for name in TRIO:
        src = db_file.parent / name.replace("db.sqlite", db_file.name)
        if src.exists():
            shutil.copy2(src, backup_dir / src.name)

    return backup_dir


@contextmanager
def open_opencode_snapshot(db_path: Path):
    """把 opencode.db 三件套复制到临时目录再读，避免与运行中的 opencode 抢锁。"""
    if not db_path.exists():
        raise FileNotFoundError(f"OpenCode 数据库不存在: {db_path}")

    snap_dir = Path(tempfile.mkdtemp(prefix="zoc-oc-snapshot-"))
    try:
        shutil.copy2(db_path, snap_dir / "opencode.db")
        for suffix in ("-wal", "-shm"):
            side = db_path.parent / (db_path.name + suffix)
            if side.exists():
                shutil.copy2(side, snap_dir / (db_path.name + suffix))
        yield snap_dir / "opencode.db"
    finally:
        shutil.rmtree(snap_dir, ignore_errors=True)


def load_opencode_sessions(db_path: Path) -> list[dict]:
    """读取 opencode 全部会话（只取所需字段）。"""
    sessions = []
    with open_opencode_snapshot(db_path) as snap:
        con = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """
                SELECT id, directory, title, time_created, time_updated,
                       time_archived, parent_id
                FROM session
                """
            ).fetchall()
        finally:
            con.close()

    for r in rows:
        sessions.append(
            {
                "id": r["id"],
                "directory": normalize_dir(r["directory"]),
                "title": r["title"] or "",
                "time_created": r["time_created"],
                "time_updated": r["time_updated"],
                "time_archived": r["time_archived"],
                "parent_id": r["parent_id"],
            }
        )
    return sessions


def load_zed_linked_session_ids(zed_db: Path) -> set[str]:
    """读取 Zed sidebar_threads 中已存在的 session_id 集合。"""
    with open_snapshot(zed_db) as snap:
        con = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT session_id FROM sidebar_threads WHERE session_id IS NOT NULL"
            ).fetchall()
        finally:
            con.close()
    return {r[0] for r in rows}


# ========== 主流程 ==========

def link_sessions(
    query: str,
    apply: bool = False,
    all_dirs: bool = False,
    target: str | None = None,
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
    include_subagents: bool = False,
) -> None:
    dry_run = not apply

    print(f"[0/6] 读取 OpenCode 会话...")
    oc_db_file = opencode_db or default_opencode_db_path()
    zed_db_file = zed_db or (default_zed_db_dir() / "db.sqlite")
    print(f"      OpenCode: {oc_db_file}")
    print(f"      Zed:      {zed_db_file}")

    sessions = load_opencode_sessions(oc_db_file)
    print(f"      共 {len(sessions)} 个会话")

    # 过滤：子会话（subagent）
    skipped_subagents = 0
    if not include_subagents:
        before = len(sessions)
        sessions = [s for s in sessions if not s["parent_id"]]
        skipped_subagents = before - len(sessions)

    # 匹配 query（归一化 + 大小写不敏感子串）
    q = normalize_dir(query.strip()).lower()
    matched = [s for s in sessions if q in s["directory"].lower()]

    print(f"\n[1/6] 匹配目录子串: {query!r}")
    if skipped_subagents:
        print(f"      已跳过 {skipped_subagents} 个子会话（subagent，--include-subagents 可强制纳入）")
    if not matched:
        print(f"错误: 未找到匹配目录的会话")
        sys.exit(1)

    # 按归一化目录分组
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in matched:
        groups[s["directory"]].append(s)

    print(f"      命中 {len(matched)} 个会话，分布在 {len(groups)} 个目录:")
    for d, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"        [{len(items):>3}] {d}")

    # 多目录消歧
    forced_target = normalize_dir(target) if target else None
    if len(groups) > 1 and not all_dirs and not forced_target:
        print(
            f"\n错误: 子串匹配到 {len(groups)} 个目录，无法确定目标。请二选一:"
            f"\n      1) 传入完整精确目录重跑"
            f"\n      2) 加 --all 逐目录处理（会话挂到各自目录）"
            f"\n      3) 加 --target <目录> 把所有命中会话挂到指定 Zed 工作区"
        )
        sys.exit(1)

    # 决定每个会话的 folder_paths
    if forced_target:
        link_dir = forced_target
    elif all_dirs:
        link_dir = None  # 各回各家
    else:
        link_dir = next(iter(groups))  # 唯一目录

    # 计算待写入 / 已存在
    print(f"\n[2/6] 快照读取 Zed 现有索引...")
    linked_ids = load_zed_linked_session_ids(zed_db_file)

    to_insert = []
    already_linked = 0
    for s in matched:
        if s["id"] in linked_ids:
            already_linked += 1
            continue
        folder = link_dir if link_dir is not None else s["directory"]
        to_insert.append((s, folder))

    print(f"      已存在（跳过）: {already_linked}")
    print(f"      待补登:         {len(to_insert)}")
    if target:
        print(f"      目标工作区:     {forced_target}")

    if not to_insert:
        print(f"\n✅ 没有需要补登的会话，无需操作")
        return

    # dry-run 预览
    print(f"\n[3/6] 预览补登计划 (dry-run)...")
    for s, folder in to_insert[:10]:
        title = (s["title"] or "(untitled)")[:40]
        print(f"      + {s['id']}  [{title}]  -> {folder}")
    if len(to_insert) > 10:
        print(f"      ... 其余 {len(to_insert) - 10} 个")

    if dry_run:
        print(f"\n[4/6] 检查进程... (dry-run 跳过写入前检查)")
        print(f"[5/6] 备份数据库... (dry-run 跳过)")
        print(f"[6/6] 写入... (dry-run 跳过)")
        print(f"\n✅ Dry-run 完成! 关闭 Zed 与 opencode 后，使用 --apply 执行实际补登")
        return

    # 进程检查
    print(f"\n[4/6] 检查 opencode / Zed 进程...")
    running = check_processes(["opencode", "zed"])
    if running:
        print(f"错误: 检测到以下进程正在运行，请完全关闭后重试:")
        for proc in running:
            print(f"      - {proc}")
        sys.exit(1)
    print(f"      ✓ 未检测到相关进程")

    # 备份
    print(f"\n[5/6] 备份 Zed 数据库...")
    backup_root = Path(os.environ.get("TEMP", ".")) / "zoc_backup"
    backup_root.mkdir(parents=True, exist_ok=True)
    zed_backup = backup_database(zed_db_file, backup_root)
    print(f"      Zed 备份: {zed_backup}")

    # 写入
    print(f"\n[6/6] 写入 sidebar_threads...")
    con = sqlite3.connect(zed_db_file)
    try:
        con.executemany(
            """
            INSERT INTO sidebar_threads
                (thread_id, session_id, agent_id, title, title_override,
                 folder_paths, folder_paths_order,
                 main_worktree_paths, main_worktree_paths_order,
                 archived, created_at, updated_at, interacted_at, remote_connection)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    uuid.uuid4().bytes,          # thread_id（BLOB 16 字节）
                    s["id"],                     # session_id：复用 opencode 原值
                    "opencode",
                    s["title"] or "",
                    None,                        # title_override
                    folder,                      # folder_paths
                    "0",                         # folder_paths_order
                    folder,                      # main_worktree_paths
                    "0",                         # main_worktree_paths_order
                    1 if s["time_archived"] is not None else 0,
                    ms_to_zed_ts(s["time_created"]),
                    ms_to_zed_ts(s["time_updated"]),
                    ms_to_zed_ts(s["time_updated"]),  # interacted_at
                    None,                        # remote_connection
                )
                for s, folder in to_insert
            ],
        )
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        print(f"      ✓ 写入 {len(to_insert)} 条，事务已提交，WAL 已落盘")
    finally:
        con.close()

    # 验证：重开只读连接复查
    print(f"\n验证...")
    linked_after = load_zed_linked_session_ids(zed_db_file)
    inserted_ids = {s["id"] for s, _ in to_insert}
    missing = inserted_ids - linked_after
    if not missing:
        print(f"      ✓ 全部 {len(inserted_ids)} 个 session_id 已在 Zed 索引中")
        print(f"\n✅ 补登完成! 请重启 Zed 并打开目标目录查看历史列表")
    else:
        print(f"      ⚠ 有 {len(missing)} 个 session_id 未被索引，请检查数据: {sorted(missing)[:5]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将某目录下已存在的 opencode 会话补登到 Zed（只写 Zed，复用原 session_id）"
    )
    parser.add_argument(
        "query",
        help="目录路径或子串（大小写不敏感；正反斜杠均可）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入 Zed 数据库（默认 dry-run 只预览）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="子串命中多个目录时全部处理（每个会话挂到自己的目录）",
    )
    parser.add_argument(
        "--target",
        help="把所有命中会话强制挂到指定 Zed 工作区目录",
    )
    parser.add_argument(
        "--include-subagents",
        action="store_true",
        help="同时补登 parent_id 非空的子会话（默认跳过）",
    )
    parser.add_argument(
        "--zed-db",
        type=Path,
        help="Zed db.sqlite 路径（默认自动探测）",
    )
    parser.add_argument(
        "--opencode-db",
        type=Path,
        help="OpenCode opencode.db 路径（默认自动探测）",
    )

    args = parser.parse_args()

    try:
        link_sessions(
            query=args.query,
            apply=args.apply,
            all_dirs=args.all,
            target=args.target,
            zed_db=args.zed_db,
            opencode_db=args.opencode_db,
            include_subagents=args.include_subagents,
        )
    except Exception as e:
        print(f"\n❌ 补登失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
