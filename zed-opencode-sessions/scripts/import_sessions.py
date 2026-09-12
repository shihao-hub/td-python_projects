#!/usr/bin/env python
"""
将归档文件中的会话导入到新机器的 Zed + OpenCode 数据库。

用法（新机器上）:
    # 1. 先 dry-run 预览（默认行为，不写库）
    uv run python scripts/import_sessions.py sessions_archive_language_projects.db --target "D:\\Code\\my_projects"

    # 2. 关闭 opencode 和 Zed，确保目标目录已存在，再实际写入
    uv run python scripts/import_sessions.py sessions_archive_language_projects.db --target "D:\\Code\\my_projects" --apply

    # 选项
    --apply           # 实际写入数据库（默认只 dry-run 预览）
    --zed-db <path>   # 指定 Zed 数据库路径（默认自动探测）
    --opencode-db <path>  # 指定 OpenCode 数据库路径（默认自动探测）

设计:
    - 默认 dry-run，只有 --apply 才写库
    - 检查 opencode/Zed 进程，要求手动关闭
    - 自动备份两个数据库的 WAL 三件套
    - 重新生成 thread_id（UUID）和 session_id（保留原格式前缀）
    - 创建或复用目标目录的 project 记录
    - 重写所有外键关系（session_id / message_id）
    - 追加模式（不删除新机器上已有的会话）
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Windows 控制台 UTF-8 输出（避免 emoji/中文乱码）
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# 添加项目路径到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from zoc.core.snapshot import default_opencode_db_path, default_zed_db_dir, TRIO


def check_processes(process_names: list[str]) -> list[str]:
    """检查指定进程是否在运行（Windows）"""
    if sys.platform != "win32":
        return []
    
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace"
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
    """备份数据库三件套"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = backup_root / f"backup_{db_file.stem}_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    for name in TRIO:
        src = db_file.parent / name.replace("db.sqlite", db_file.name)
        if src.exists():
            shutil.copy2(src, backup_dir / src.name)
    
    return backup_dir


def generate_session_id() -> str:
    """生成新的 session_id（保留 opencode 的格式）"""
    # opencode 的 session_id 格式: ses_<22字符base64>
    import base64
    random_bytes = uuid.uuid4().bytes + uuid.uuid4().bytes[:10]  # 22 bytes
    b64 = base64.urlsafe_b64encode(random_bytes).decode('ascii').rstrip('=')
    return f"ses_{b64}"


def generate_message_id() -> str:
    """生成新的 message_id（保留 opencode 的格式）"""
    # opencode 的 message_id 格式: msg_<22字符base64>
    import base64
    random_bytes = uuid.uuid4().bytes + uuid.uuid4().bytes[:10]
    b64 = base64.urlsafe_b64encode(random_bytes).decode('ascii').rstrip('=')
    return f"msg_{b64}"


def generate_part_id() -> str:
    """生成新的 part_id（保留 opencode 的格式）"""
    # opencode 的 part_id 格式: prt_<22字符base64>
    import base64
    random_bytes = uuid.uuid4().bytes + uuid.uuid4().bytes[:10]
    b64 = base64.urlsafe_b64encode(random_bytes).decode('ascii').rstrip('=')
    return f"prt_{b64}"


def get_or_create_project(
    oc_con: sqlite3.Connection,
    target_dir: str
) -> str:
    """获取或创建目标目录的 project 记录，返回 project_id"""
    oc_con.row_factory = sqlite3.Row
    
    # 检查是否已存在
    existing = oc_con.execute(
        "SELECT id FROM project WHERE worktree = ?",
        (target_dir,)
    ).fetchone()
    
    if existing:
        return existing["id"]
    
    # 创建新 project（模仿 opencode 的逻辑）
    import hashlib
    # 使用 uuid + 路径的混合 hash 作为 project_id
    project_id = hashlib.sha1(
        (str(uuid.uuid4()) + target_dir).encode()
    ).hexdigest()
    
    now_ms = int(datetime.now().timestamp() * 1000)
    
    oc_con.execute(
        """INSERT INTO project 
           (id, worktree, vcs, name, time_created, time_updated, sandboxes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (project_id, target_dir, "git", None, now_ms, now_ms, "[]")
    )
    
    # 同时插入 project_directory
    oc_con.execute(
        """INSERT INTO project_directory 
           (project_id, directory, time_created)
           VALUES (?, ?, ?)""",
        (project_id, target_dir, now_ms)
    )
    
    return project_id


def import_sessions(
    archive_file: Path,
    target_dir: str,
    apply: bool = False,
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
) -> None:
    """从归档文件导入会话（默认 dry-run，apply=True 才实际写库）"""
    dry_run = not apply
    
    # 0. 检查归档文件
    if not archive_file.exists():
        print(f"错误: 归档文件不存在: {archive_file}")
        sys.exit(1)
    
    print(f"[0/7] 读取归档文件: {archive_file}...")
    archive_con = sqlite3.connect(archive_file)
    archive_con.row_factory = sqlite3.Row
    
    # 读取元数据
    meta = {
        r["key"]: r["value"]
        for r in archive_con.execute("SELECT key, value FROM _archive_meta")
    }
    
    print(f"      导出时间: {meta.get('export_time', 'N/A')}")
    print(f"      源项目: {meta.get('source_project', 'N/A')}")
    print(f"      会话数: {meta.get('session_count', 'N/A')}")
    print(f"      消息数: {meta.get('message_count', 'N/A')}")
    print(f"      内容数: {meta.get('part_count', 'N/A')}")
    
    # 读取数据
    threads = archive_con.execute("SELECT * FROM zed_threads").fetchall()
    sessions = archive_con.execute("SELECT * FROM opencode_sessions").fetchall()
    messages = archive_con.execute("SELECT * FROM opencode_messages").fetchall()
    parts = archive_con.execute("SELECT * FROM opencode_parts").fetchall()
    
    archive_con.close()
    
    print(f"\n[1/7] 检查目标目录...")
    target_path = Path(target_dir)
    if not target_path.exists():
        print(f"错误: 目标目录不存在: {target_dir}")
        print(f"提示: 请先创建该目录")
        sys.exit(1)
    
    print(f"      目标: {target_dir}")
    
    # 2. 检查进程
    print(f"\n[2/7] 检查 opencode 和 Zed 进程...")
    running = check_processes(["opencode", "zed"])
    if running and not dry_run:
        print(f"错误: 检测到以下进程正在运行，请手动关闭后重试:")
        for proc in running:
            print(f"      - {proc}")
        sys.exit(1)
    elif running:
        print(f"      警告: 检测到进程运行（dry-run 模式忽略）: {', '.join(running)}")
    else:
        print(f"      ✓ 未检测到相关进程")
    
    # 3. 定位数据库
    print(f"\n[3/7] 定位数据库...")
    zed_db_file = zed_db or (default_zed_db_dir() / "db.sqlite")
    oc_db_file = opencode_db or default_opencode_db_path()
    
    if not zed_db_file.exists():
        print(f"错误: Zed 数据库不存在: {zed_db_file}")
        sys.exit(1)
    
    if not oc_db_file.exists():
        print(f"错误: OpenCode 数据库不存在: {oc_db_file}")
        sys.exit(1)
    
    print(f"      Zed: {zed_db_file}")
    print(f"      OpenCode: {oc_db_file}")
    
    # 4. 备份（仅非 dry-run）
    if not dry_run:
        print(f"\n[4/7] 备份数据库...")
        backup_root = Path(os.environ.get("TEMP", ".")) / "zoc_backup"
        backup_root.mkdir(parents=True, exist_ok=True)
        
        zed_backup = backup_database(zed_db_file, backup_root)
        oc_backup = backup_database(oc_db_file, backup_root)
        
        print(f"      Zed 备份: {zed_backup}")
        print(f"      OpenCode 备份: {oc_backup}")
    else:
        print(f"\n[4/7] 备份数据库... (dry-run 跳过)")
    
    # 5. 生成 ID 映射
    print(f"\n[5/7] 生成 ID 映射...")
    
    # session_id 映射: old -> new
    session_id_map = {s["id"]: generate_session_id() for s in sessions}
    
    # message_id 映射: old -> new
    message_id_map = {m["id"]: generate_message_id() for m in messages}
    
    print(f"      {len(session_id_map)} 个 session_id")
    print(f"      {len(message_id_map)} 个 message_id")
    print(f"      {len(parts)} 个 part (将生成新 id)")
    
    # 6. 写入数据（或预览）
    if dry_run:
        print(f"\n[6/7] 预览导入计划 (dry-run)...")
        print(f"      将导入 {len(threads)} 个 thread 到 Zed")
        print(f"      将导入 {len(sessions)} 个 session 到 OpenCode")
        print(f"      将导入 {len(messages)} 个 message 到 OpenCode")
        print(f"      将导入 {len(parts)} 个 part 到 OpenCode")
        print(f"      目标目录: {target_dir}")
        print(f"\n[7/7] 验证... (dry-run 跳过)")
        print(f"\n✅ Dry-run 完成! 使用 --apply 执行实际导入")
        return
    
    print(f"\n[6/7] 写入数据...")
    
    # 打开目标数据库（写模式）
    zed_con = sqlite3.connect(zed_db_file)
    oc_con = sqlite3.connect(oc_db_file)
    
    try:
        # 6.1 在 OpenCode 中获取或创建 project
        project_id = get_or_create_project(oc_con, target_dir)
        print(f"      project_id: {project_id}")
        
        # 6.2 写入 OpenCode session
        for s in sessions:
            old_sid = s["id"]
            new_sid = session_id_map[old_sid]
            
            oc_con.execute(
                """INSERT INTO session 
                   (id, project_id, workspace_id, parent_id, slug, directory, path, 
                    title, version, share_url, summary_additions, summary_deletions, 
                    summary_files, summary_diffs, metadata, cost, tokens_input, 
                    tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, 
                    revert, permission, agent, model, time_created, time_updated, 
                    time_compacting, time_archived)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_sid,
                    project_id,  # 新的 project_id
                    None,  # workspace_id
                    None,  # parent_id
                    s["id"].split("_")[1][:12],  # slug（从 session_id 生成）
                    target_dir,  # 新的 directory
                    "",  # path
                    s["title"],
                    s["version"],
                    None,  # share_url
                    0, 0, 0,  # summary_*
                    None, None,  # summary_diffs, metadata
                    0.0,  # cost
                    0, 0, 0, 0, 0,  # tokens_*
                    None, None,  # revert, permission
                    s["agent"],
                    s["model"],
                    s["time_created"],
                    s["time_created"],  # time_updated
                    None,  # time_compacting
                    s["time_archived"],
                )
            )
        
        print(f"      ✓ 写入 {len(sessions)} 个 session")
        
        # 6.3 写入 OpenCode message
        for m in messages:
            old_sid = m["session_id"]
            new_sid = session_id_map.get(old_sid)
            
            if not new_sid:
                continue  # 跳过未映射的 session
            
            old_mid = m["id"]
            new_mid = message_id_map[old_mid]
            
            oc_con.execute(
                """INSERT INTO message 
                   (id, session_id, time_created, time_updated, data)
                   VALUES (?, ?, ?, ?, ?)""",
                (new_mid, new_sid, m["time_created"], m["time_updated"], m["data"])
            )
        
        print(f"      ✓ 写入 {len(messages)} 个 message")
        
        # 6.4 写入 OpenCode part
        for p in parts:
            old_sid = p["session_id"]
            new_sid = session_id_map.get(old_sid)
            
            if not new_sid:
                continue
            
            old_mid = p["message_id"]
            new_mid = message_id_map.get(old_mid)
            
            if not new_mid:
                continue
            
            new_pid = generate_part_id()
            
            oc_con.execute(
                """INSERT INTO part 
                   (id, message_id, session_id, time_created, time_updated, data)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (new_pid, new_mid, new_sid, p["time_created"], p["time_updated"], p["data"])
            )
        
        print(f"      ✓ 写入 {len(parts)} 个 part")
        
        # 6.5 写入 Zed threads
        for t in threads:
            old_sid = t["session_id"]
            new_sid = session_id_map.get(old_sid)
            
            if not new_sid:
                continue
            
            new_tid = uuid.uuid4().bytes  # 新的 thread_id
            
            zed_con.execute(
                """INSERT INTO sidebar_threads 
                   (thread_id, session_id, agent_id, title, title_override, 
                    folder_paths, folder_paths_order, main_worktree_paths, main_worktree_paths_order,
                    archived, created_at, updated_at, interacted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_tid, new_sid, t["agent_id"], t["title"], t["title_override"],
                    target_dir,  # folder_paths
                    "0",         # folder_paths_order
                    target_dir,  # main_worktree_paths（补上这一列）
                    "0",         # main_worktree_paths_order（补上这一列）
                    t["archived"], t["created_at"], t["updated_at"], t["interacted_at"],
                )
            )
        
        print(f"      ✓ 写入 {len(threads)} 个 thread")
        
        # 提交事务
        oc_con.commit()
        zed_con.commit()
        
        # WAL checkpoint
        oc_con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        zed_con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        
        print(f"      ✓ 事务已提交，WAL 已落盘")
        
    finally:
        oc_con.close()
        zed_con.close()
    
    # 7. 验证
    print(f"\n[7/7] 验证...")
    zed_con = sqlite3.connect(f"file:{zed_db_file}?mode=ro", uri=True)
    oc_con = sqlite3.connect(f"file:{oc_db_file}?mode=ro&immutable=1", uri=True)
    
    zed_count = zed_con.execute(
        "SELECT count(*) c FROM sidebar_threads WHERE folder_paths = ?",
        (target_dir,)
    ).fetchone()[0]
    
    oc_count = oc_con.execute(
        "SELECT count(*) c FROM session WHERE directory = ?",
        (target_dir,)
    ).fetchone()[0]
    
    zed_con.close()
    oc_con.close()
    
    print(f"      Zed threads: {zed_count}")
    print(f"      OpenCode sessions: {oc_count}")
    
    if zed_count >= len(threads) and oc_count >= len(sessions):
        print(f"\n✅ 导入完成!")
    else:
        print(f"\n⚠️ 导入可能不完整，请检查数据")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="导入会话归档文件到新机器"
    )
    parser.add_argument(
        "archive",
        type=Path,
        help="归档文件路径（sessions_archive_*.db）"
    )
    parser.add_argument(
        "--target",
        required=True,
        help="目标目录路径（必须已存在）"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入数据库（默认 dry-run 只预览）"
    )
    parser.add_argument(
        "--zed-db",
        type=Path,
        help="Zed db.sqlite 路径（默认自动探测）"
    )
    parser.add_argument(
        "--opencode-db",
        type=Path,
        help="OpenCode opencode.db 路径（默认自动探测）"
    )
    
    args = parser.parse_args()
    
    try:
        import_sessions(
            archive_file=args.archive,
            target_dir=args.target,
            apply=args.apply,
            zed_db=args.zed_db,
            opencode_db=args.opencode_db,
        )
    except Exception as e:
        print(f"\n❌ 导入失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
