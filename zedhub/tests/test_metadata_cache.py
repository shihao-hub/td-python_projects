from __future__ import annotations

import json
from pathlib import Path

from zedhub.core.metadata_cache import MetadataCacheService


def test_metadata_cache_basic_sessions(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata_cache.db"
    cache = MetadataCacheService(db_path)

    # 模拟 claude 目录
    claude_home = tmp_path / "home"
    projects = claude_home / ".claude" / "projects" / "test-proj"
    projects.mkdir(parents=True, exist_ok=True)

    session_file = projects / "sess-1.jsonl"
    session_file.write_text(
        json.dumps({"timestamp": "2026-10-27T10:00:00Z", "cwd": str(tmp_path / "work")}) + "\n"
        + json.dumps({"type": "user", "message": {"content": "Hello world"}}) + "\n",
        encoding="utf-8",
    )

    # 首次同步
    sessions1 = cache.get_sessions(["claude-code"], home=claude_home)
    assert len(sessions1) == 1
    assert sessions1[0].external_id == "sess-1"
    assert sessions1[0].title == "Hello world"
    assert sessions1[0].source_id == "claude-code"

    # 二次读取（缓存命中）
    sessions2 = cache.get_sessions(["claude-code"], home=claude_home)
    assert len(sessions2) == 1
    assert sessions2[0].external_id == "sess-1"

    # 文件修改后自动更新
    session_file.write_text(
        json.dumps({"timestamp": "2026-10-27T10:00:00Z", "cwd": str(tmp_path / "work")}) + "\n"
        + json.dumps({"type": "user", "message": {"content": "Updated prompt"}}) + "\n",
        encoding="utf-8",
    )
    sessions3 = cache.get_sessions(["claude-code"], home=claude_home)
    assert len(sessions3) == 1
    assert sessions3[0].title == "Updated prompt"

    # 文件删除后自动清除
    session_file.unlink()
    sessions4 = cache.get_sessions(["claude-code"], home=claude_home)
    assert len(sessions4) == 0


def test_metadata_cache_antigravity_sync(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata_cache.db"
    cache = MetadataCacheService(db_path)

    agy_home = tmp_path / "home"
    v_root = agy_home / ".gemini" / "antigravity-acp"
    conv = v_root / "conversations"
    brain = v_root / "brain"
    anno = v_root / "annotations"
    conv.mkdir(parents=True, exist_ok=True)
    brain.mkdir(parents=True, exist_ok=True)
    anno.mkdir(parents=True, exist_ok=True)

    sid = "4ec40ecb-27a5-45c7-a6a0-36a5b8b312dc"
    # 模拟 conversation meta
    (conv / f"{sid}.meta").write_text(
        json.dumps({"cwd": str(tmp_path / "repo")}), encoding="utf-8"
    )
    # 模拟 annotation
    (anno / f"{sid}.pbtxt").write_text(
        'title: "Antigravity Session Title"\narchived: false\n', encoding="utf-8"
    )
    # 模拟 transcript
    tr_dir = brain / sid / ".system_generated" / "logs"
    tr_dir.mkdir(parents=True, exist_ok=True)
    (tr_dir / "transcript.jsonl").write_text(
        json.dumps({"type": "USER_INPUT", "content": "What is going on?", "created_at": "2026-10-27T10:00:00Z"}) + "\n",
        encoding="utf-8",
    )

    # 首次同步
    sessions = cache.get_sessions(["antigravity"], home=agy_home)
    assert len(sessions) == 1
    assert sessions[0].external_id == sid
    assert sessions[0].title == "Antigravity Session Title"
    assert sessions[0].source_id == "antigravity"

    # 二次读取（缓存命中）
    sessions2 = cache.get_sessions(["antigravity"], home=agy_home)
    assert len(sessions2) == 1
    assert sessions2[0].external_id == sid


def test_metadata_cache_threads_sync(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata_cache.db"
    cache = MetadataCacheService(db_path)

    zed_dir = tmp_path / "zed_db"
    zed_dir.mkdir()
    db_file = zed_dir / "db.sqlite"

    # 不存在时返回空
    assert cache.get_threads(db_file) == []

    # 伪造一个真实的 Zed sqlite 库
    import sqlite3

    con = sqlite3.connect(db_file)
    con.execute(
        """
        CREATE TABLE sidebar_threads (
            thread_id BLOB PRIMARY KEY,
            session_id TEXT,
            agent_id TEXT,
            title TEXT,
            title_override TEXT,
            updated_at TEXT,
            created_at TEXT,
            folder_paths TEXT,
            archived INTEGER,
            interacted_at TEXT
        );
        """
    )
    # 插入一条测试数据，注意 thread_id 存 16 字节 blob，folder_paths 换行分隔
    con.execute(
        """
        INSERT INTO sidebar_threads (
            thread_id, session_id, agent_id, title, title_override,
            updated_at, created_at, folder_paths, archived, interacted_at
        ) VALUES (
            ?, 'sid-1', 'claude-acp', 'Test Thread', NULL,
            '2026-10-27T10:00:00.000000000+00:00', '2026-10-27T10:00:00.000000000+00:00',
            '/tmp/proj', 0, '2026-10-27T10:00:00.000000000+00:00'
        )
        """,
        (b"\x01" * 16,),
    )
    con.commit()
    con.close()

    # 首次同步
    threads1 = cache.get_threads(db_file)
    assert len(threads1) == 1
    assert threads1[0].agent_id == "claude-acp"
    assert threads1[0].title == "Test Thread"
    assert threads1[0].projects == ["/tmp/proj"]

    # 二次读取（缓存命中）
    threads2 = cache.get_threads(db_file)
    assert len(threads2) == 1
    assert threads2[0].id == threads1[0].id
    assert threads2[0].projects == ["/tmp/proj"]
