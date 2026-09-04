"""把源工作区目录的 Zed agent 会话镜像同步到目标工作区（可反复执行）。

用法（在 zedhub 项目根目录）：
  uv run python scripts\\copy_sessions.py                      # 对真实库 dry-run
  uv run python scripts\\copy_sessions.py --apply              # 写真实库（要求 Zed 完全退出，自动备份）
  uv run python scripts\\copy_sessions.py --db <db路径> --apply # 对库副本自测

同步语义（镜像）：
  - 源目录中未归档、目标下尚无副本（按 session_id 匹配）的会话 -> INSERT 副本
    （新随机 thread_id；folder_paths / main_worktree_paths 四列改写为目标单路径）
  - 已有副本 -> UPDATE，元数据列刷新为源当前值（副本 thread_id 不变）
  - 已归档且无副本的源会话 -> 跳过；目标下源已不存在的孤儿副本 -> 不动，仅报告
  - session_id 为 NULL 的源会话 -> 跳过并警告（无法幂等匹配）
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from zedhub.core.model import parse_ts
from zedhub.core.repo import REQUIRED_COLUMNS
from zedhub.core.snapshot import TRIO, SnapshotError, default_db_dir, open_snapshot

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_SOURCES = [
    r"C:\WorkingProjects\go_projects",
    r"C:\WorkingProjects\python_projects",
    r"C:\WorkingProjects\rust_projects",
]
DEFAULT_TARGET = r"C:\WorkingProjects\language_projects"
PATH_COLS = ("folder_paths", "folder_paths_order", "main_worktree_paths", "main_worktree_paths_order")
UPDATE_EXCLUDED = {"thread_id", "session_id", *PATH_COLS}


def _die(msg: str) -> None:
    print(f"[copy_sessions] 错误: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _paths_of(raw: str | None) -> list[str]:
    return [p.strip() for p in (raw or "").split("\n") if p.strip()]


def _fmt_ts(raw) -> str:
    dt = parse_ts(raw)
    return dt.astimezone().strftime("%m-%d %H:%M") if dt else (str(raw or "")[:16] or "-")


def _fmt_title(raw) -> str:
    return (str(raw).strip() if raw and str(raw).strip() else "(untitled)")[:46]


def zed_processes() -> list[str]:
    out = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace"
    ).stdout
    names = []
    for line in out.splitlines():
        first = line.split('","', 1)[0].strip('"')
        if first and "zed" in first.lower():
            names.append(first)
    return names


def backup_trio(db_file: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path(os.environ.get("TEMP", ".")) / "opencode" / f"zeddb_backup_{ts}"
    dest.mkdir(parents=True)
    for name in TRIO:
        src = db_file.parent / name
        if src.exists():
            shutil.copy2(src, dest / name)
    return dest


def table_columns(con: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in con.execute("PRAGMA table_info(sidebar_threads)")]


def check_schema(con: sqlite3.Connection) -> list[str]:
    cols = table_columns(con)
    missing = REQUIRED_COLUMNS - set(cols)
    if missing:
        _die(f"sidebar_threads 缺列: {', '.join(sorted(missing))}（Zed schema 变了？）")
    for idx in con.execute("PRAGMA index_list(sidebar_threads)").fetchall():
        if not idx["unique"]:
            continue
        idx_cols = [c["name"] for c in con.execute(f'PRAGMA index_info("{idx["name"]}")')]
        if idx_cols == ["session_id"]:
            _die("session_id 存在唯一索引，复制副本会违反约束；需降级为『迁移改路径』方案")
    return cols


def thread_id_tables(con: sqlite3.Connection) -> list[str]:
    tables = [r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return [
        t
        for t in tables
        if t != "sidebar_threads"
        and any(c["name"] == "thread_id" for c in con.execute(f'PRAGMA table_info("{t}")'))
    ]


def analyze(con: sqlite3.Connection, sources: list[str], target: str) -> dict:
    con.row_factory = sqlite3.Row
    cols = check_schema(con)
    tid_tables = thread_id_tables(con)
    rows = con.execute("SELECT * FROM sidebar_threads").fetchall()

    src_rows: dict[str, sqlite3.Row] = {}
    null_sid: list[sqlite3.Row] = []
    for r in rows:
        if not (_paths_of(r["folder_paths"]) and set(_paths_of(r["folder_paths"])) & set(sources)):
            continue
        sid = r["session_id"]
        if not sid:
            null_sid.append(r)
            continue
        prev = src_rows.get(sid)
        if prev is None or str(r["updated_at"] or "") > str(prev["updated_at"] or ""):
            src_rows[sid] = r

    tgt_rows: dict[str, sqlite3.Row] = {}
    orphans: list[sqlite3.Row] = []
    for r in rows:
        if _paths_of(r["folder_paths"]) != [target]:
            continue
        sid = r["session_id"]
        if sid and sid in src_rows:
            tgt_rows[sid] = r
        else:
            orphans.append(r)

    inserts, updates, skips = [], [], []
    for sid, src in src_rows.items():
        tgt = tgt_rows.get(sid)
        if tgt is None:
            if src["archived"]:
                skips.append(src)
            else:
                inserts.append(src)
            continue
        changed = [
            c for c in cols
            if c not in UPDATE_EXCLUDED and tgt[c] != src[c]
        ]
        if changed:
            updates.append((src, tgt, changed))

    order = lambda r: str(r["updated_at"] or "")
    return {
        "cols": cols,
        "tid_tables": tid_tables,
        "inserts": sorted(inserts, key=order, reverse=True),
        "updates": sorted(updates, key=lambda u: order(u[0]), reverse=True),
        "skips": skips,
        "null_sid": null_sid,
        "orphans": orphans,
        "matched": len(tgt_rows),
    }


def render_plan(plan: dict, sources: list[str], target: str) -> None:
    print(f"目标: {target}")
    print(f"源  : {' | '.join(Path(s).name for s in sources)}")
    if plan["tid_tables"]:
        print(f"提示: 其它含 thread_id 的表（副本无对应行，仅提示）: {', '.join(plan['tid_tables'])}")
    for r in plan["null_sid"]:
        print(f"警告: session_id 为 NULL，跳过 -> {_fmt_title(r['title_override'] or r['title'])}")
    print(f"\n== INSERT 待复制 {len(plan['inserts'])} 条 ==")
    for r in plan["inserts"]:
        src_dir = Path(_paths_of(r["folder_paths"])[0]).name
        print(f"  [{r['agent_id']:<10}] {_fmt_title(r['title_override'] or r['title'])}  {_fmt_ts(r['updated_at'])}  <- {src_dir}")
    print(f"\n== UPDATE 待覆盖 {len(plan['updates'])} 条 ==")
    for src, _tgt, changed in plan["updates"]:
        print(f"  [{src['agent_id']:<10}] {_fmt_title(src['title_override'] or src['title'])}  改动: {', '.join(changed)}")
    print(f"\n跳过(已归档无副本): {len(plan['skips'])}  孤儿副本(源已不存在): {len(plan['orphans'])}  NULL会话: {len(plan['null_sid'])}")


def apply_ops(con: sqlite3.Connection, plan: dict, target: str) -> tuple[int, int]:
    cols = [c for c in plan["cols"] if c != "thread_id"]
    overrides = {
        "folder_paths": target,
        "folder_paths_order": "0",
        "main_worktree_paths": target,
        "main_worktree_paths_order": "0",
    }
    n_ins = n_upd = 0
    con.execute("BEGIN")
    for src in plan["inserts"]:
        values = [uuid.uuid4().bytes] + [
            overrides.get(c, src[c]) for c in cols
        ]
        con.execute(
            f"INSERT INTO sidebar_threads (thread_id, {', '.join(cols)}) VALUES ({', '.join('?' * (len(cols) + 1))})",
            values,
        )
        n_ins += 1
    for src, tgt, changed in plan["updates"]:
        con.execute(
            f"UPDATE sidebar_threads SET {', '.join(f'{c} = ?' for c in changed)} WHERE thread_id = ?",
            [src[c] for c in changed] + [tgt["thread_id"]],
        )
        n_upd += 1
    con.commit()
    return n_ins, n_upd


def verify(db_file: Path, target: str, expected_copies: int) -> None:
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    total = con.execute(
        "SELECT count(*) c FROM sidebar_threads WHERE folder_paths = ?", (target,)
    ).fetchone()["c"]
    con.close()
    status = "OK" if total >= expected_copies else "MISMATCH"
    print(f"复查: 目标下共 {total} 行（预期副本 {expected_copies}）[{status}]")


def main() -> None:
    ap = argparse.ArgumentParser(description="镜像同步 Zed 会话到目标工作区")
    ap.add_argument("--source", action="append", default=None, help="源目录，可多次（默认 go/python/rust_projects）")
    ap.add_argument("--target", default=DEFAULT_TARGET, help="目标工作区路径")
    ap.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run）")
    ap.add_argument("--db", default=None, help="db.sqlite 或其目录（默认 Zed 真实库）")
    args = ap.parse_args()
    sources = args.source or DEFAULT_SOURCES

    db_arg = Path(args.db) if args.db else default_db_dir() / "db.sqlite"
    if db_arg.is_dir():
        db_arg = db_arg / "db.sqlite"
    if not db_arg.exists():
        _die(f"数据库不存在: {db_arg}")
    is_live = args.db is None

    if args.apply and is_live:
        procs = zed_processes()
        if procs:
            _die(f"Zed 正在运行（{', '.join(procs)}），请完全退出后重试")
        backup = backup_trio(db_arg)
        print(f"已备份三件套 -> {backup}")

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"== copy_sessions {mode} | db: {db_arg}{' (live)' if is_live else ' (copy)'} ==")

    if args.apply:
        con = sqlite3.connect(db_arg)
        try:
            plan = analyze(con, sources, args.target)
            render_plan(plan, sources, args.target)
            n_ins, n_upd = apply_ops(con, plan, args.target)
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            print(f"\n已写入: INSERT {n_ins} / UPDATE {n_upd}，WAL 已落盘")
        finally:
            con.close()
        copies = len(plan["inserts"]) + plan["matched"] + len(plan["orphans"])
        verify(db_arg, args.target, copies)
    else:
        try:
            with open_snapshot(args.db) as snap:
                con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
                try:
                    plan = analyze(con, sources, args.target)
                    render_plan(plan, sources, args.target)
                finally:
                    con.close()
        except SnapshotError as exc:
            _die(str(exc))


if __name__ == "__main__":
    main()
