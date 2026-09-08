#!/usr/bin/env python
"""
导出指定目录的 Zed + OpenCode 会话到独立的 SQLite 归档文件。

用法:
    uv run python scripts/export_sessions.py language_projects -o sessions_archive_language_projects.db
    uv run python scripts/export_sessions.py "C:\\WorkingProjects\\go_projects" -o go_sessions.db --archived

设计:
    - 导出的 SQLite 文件包含完整的 session/message/part 数据
    - 文件命名建议: sessions_archive_*.db (避免被 .gitignore)
    - 只导出未归档的 opencode 会话（除非指定 --archived）
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Windows 控制台 UTF-8 输出（避免 emoji/中文乱码）
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# 添加项目路径到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from zoc.core.model import OpencodeRepo, ZedRepo
from zoc.core.snapshot import default_opencode_db_path


def create_archive_schema(con: sqlite3.Connection) -> None:
    """创建归档数据库的表结构"""
    con.executescript("""
        -- 元数据表
        CREATE TABLE IF NOT EXISTS _archive_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        
        -- Zed sidebar_threads（完整结构）
        CREATE TABLE IF NOT EXISTS zed_threads (
            thread_id BLOB PRIMARY KEY,
            session_id TEXT,
            agent_id TEXT,
            title TEXT,
            title_override TEXT,
            folder_paths TEXT,
            folder_paths_order TEXT,
            archived INTEGER,
            created_at TEXT,
            updated_at TEXT,
            interacted_at TEXT
        );
        
        -- OpenCode session 表
        CREATE TABLE IF NOT EXISTS opencode_sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            directory TEXT,
            title TEXT,
            version TEXT,
            agent TEXT,
            model TEXT,
            time_created INTEGER,
            time_archived INTEGER
        );
        
        -- OpenCode message 表
        CREATE TABLE IF NOT EXISTS opencode_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            time_created INTEGER,
            time_updated INTEGER,
            data TEXT
        );
        
        -- OpenCode part 表
        CREATE TABLE IF NOT EXISTS opencode_parts (
            id TEXT PRIMARY KEY,
            message_id TEXT,
            session_id TEXT,
            time_created INTEGER,
            time_updated INTEGER,
            data TEXT
        );
        
        -- 索引
        CREATE INDEX IF NOT EXISTS idx_zed_threads_session ON zed_threads(session_id);
        CREATE INDEX IF NOT EXISTS idx_messages_session ON opencode_messages(session_id);
        CREATE INDEX IF NOT EXISTS idx_parts_session ON opencode_parts(session_id);
        CREATE INDEX IF NOT EXISTS idx_parts_message ON opencode_parts(message_id);
    """)


def export_sessions(
    project: str,
    output: Path,
    include_archived: bool = False,
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
) -> None:
    """导出会话到归档文件"""
    
    # 1. 查询 Zed threads
    print(f"[1/4] 查询 Zed threads (project={project}, archived={include_archived})...")
    zed_repo = ZedRepo(zed_db)
    threads = zed_repo.list_threads(
        project=project,
        agent="opencode",
        archived=include_archived,
    )
    
    if not threads:
        print("错误: 未找到匹配的会话")
        sys.exit(1)
    
    print(f"      找到 {len(threads)} 个 thread")
    
    # 提取所有 session_id
    session_ids = [t.session_id for t in threads if t.session_id]
    if not session_ids:
        print("错误: 所有 thread 都没有 session_id")
        sys.exit(1)
    
    print(f"      其中 {len(session_ids)} 个有 session_id")
    
    # 2. 查询 OpenCode 数据
    print(f"[2/4] 查询 OpenCode 数据...")
    oc_db = opencode_db or default_opencode_db_path()
    uri = f"file:{oc_db.as_posix()}?mode=ro&immutable=1"
    oc_con = sqlite3.connect(uri, uri=True)
    oc_con.row_factory = sqlite3.Row
    
    # 查询 session
    placeholders = ",".join("?" * len(session_ids))
    sessions = oc_con.execute(
        f"SELECT * FROM session WHERE id IN ({placeholders})",
        session_ids
    ).fetchall()
    
    if not sessions:
        print("错误: OpenCode 中未找到对应的 session 记录")
        sys.exit(1)
    
    print(f"      找到 {len(sessions)} 个 session")
    
    # 查询 message
    messages = oc_con.execute(
        f"SELECT * FROM message WHERE session_id IN ({placeholders})",
        session_ids
    ).fetchall()
    print(f"      找到 {len(messages)} 个 message")
    
    # 查询 part
    parts = oc_con.execute(
        f"SELECT * FROM part WHERE session_id IN ({placeholders})",
        session_ids
    ).fetchall()
    print(f"      找到 {len(parts)} 个 part")
    
    oc_con.close()
    
    # 3. 创建归档数据库
    print(f"[3/4] 创建归档文件: {output}...")
    if output.exists():
        print(f"      警告: 文件已存在，将覆盖")
        output.unlink()
    
    archive_con = sqlite3.connect(output)
    create_archive_schema(archive_con)
    
    # 写入元数据
    archive_con.executemany(
        "INSERT INTO _archive_meta (key, value) VALUES (?, ?)",
        [
            ("export_time", datetime.now().isoformat()),
            ("source_project", project),
            ("thread_count", str(len(threads))),
            ("session_count", str(len(sessions))),
            ("message_count", str(len(messages))),
            ("part_count", str(len(parts))),
            ("include_archived", str(include_archived)),
        ]
    )
    
    # 4. 写入数据
    print(f"[4/4] 写入数据...")
    
    # 写入 Zed threads
    with open_snapshot(zed_db) as snap:
        zed_con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
        zed_con.row_factory = sqlite3.Row
        
        for t in threads:
            # 重新查询完整行（包含 BLOB thread_id）
            row = zed_con.execute(
                "SELECT * FROM sidebar_threads WHERE session_id = ?",
                (t.session_id,)
            ).fetchone()
            
            if row:
                archive_con.execute(
                    """INSERT INTO zed_threads 
                       (thread_id, session_id, agent_id, title, title_override, 
                        folder_paths, folder_paths_order, archived, created_at, 
                        updated_at, interacted_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["thread_id"],
                        row["session_id"],
                        row["agent_id"],
                        row["title"],
                        row["title_override"],
                        row["folder_paths"],
                        row["folder_paths_order"],
                        row["archived"],
                        row["created_at"],
                        row["updated_at"],
                        row["interacted_at"],
                    )
                )
        
        zed_con.close()
    
    print(f"      写入 {len(threads)} 个 zed_thread")
    
    # 写入 OpenCode session
    oc_con = sqlite3.connect(uri, uri=True)
    oc_con.row_factory = sqlite3.Row
    
    for s in sessions:
        archive_con.execute(
            """INSERT INTO opencode_sessions 
               (id, project_id, directory, title, version, agent, model, 
                time_created, time_archived)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                s["id"],
                s["project_id"],
                s["directory"],
                s["title"],
                s["version"],
                s["agent"],
                s["model"],
                s["time_created"],
                s["time_archived"],
            )
        )
    print(f"      写入 {len(sessions)} 个 session")
    
    # 写入 message
    for m in messages:
        archive_con.execute(
            """INSERT INTO opencode_messages 
               (id, session_id, time_created, time_updated, data)
               VALUES (?, ?, ?, ?, ?)""",
            (m["id"], m["session_id"], m["time_created"], m["time_updated"], m["data"])
        )
    print(f"      写入 {len(messages)} 个 message")
    
    # 写入 part
    for p in parts:
        archive_con.execute(
            """INSERT INTO opencode_parts 
               (id, message_id, session_id, time_created, time_updated, data)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (p["id"], p["message_id"], p["session_id"], p["time_created"], 
             p["time_updated"], p["data"])
        )
    print(f"      写入 {len(parts)} 个 part")
    
    oc_con.close()
    archive_con.commit()
    archive_con.close()
    
    # 输出统计
    size_mb = output.stat().st_size / 1024 / 1024
    print(f"\n✅ 导出完成!")
    print(f"   文件: {output}")
    print(f"   大小: {size_mb:.2f} MB")
    print(f"   会话: {len(sessions)} 个")
    print(f"   消息: {len(messages)} 条")
    print(f"   内容: {len(parts)} 个 part")


# 需要导入 open_snapshot
from zoc.core.snapshot import open_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(
        description="导出 Zed + OpenCode 会话到归档文件"
    )
    parser.add_argument(
        "project",
        help="项目路径子串（不区分大小写）"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        required=True,
        help="输出文件路径（建议命名: sessions_archive_*.db）"
    )
    parser.add_argument(
        "--archived",
        action="store_true",
        help="包含已归档的会话"
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
        export_sessions(
            project=args.project,
            output=args.output,
            include_archived=args.archived,
            zed_db=args.zed_db,
            opencode_db=args.opencode_db,
        )
    except Exception as e:
        print(f"\n❌ 导出失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
