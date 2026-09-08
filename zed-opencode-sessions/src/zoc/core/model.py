"""SQLite ORM 访问层：ZedRepo + OpencodeRepo。"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..schema import (
    Message,
    MessagePart,
    SessionContent,
    SessionMeta,
    Thread,
)
from .errors import NotFoundError, SchemaError
from .snapshot import default_opencode_db_path, default_zed_db_dir, open_snapshot


# ========== 辅助函数 ==========

def _blob_to_uuid(blob: bytes | None) -> str:
    """Zed 的 thread_id 是 16 字节 BLOB，转 uuid 字符串"""
    if isinstance(blob, (bytes, bytearray)) and len(blob) == 16:
        return str(uuid.UUID(bytes=bytes(blob)))
    return (blob or b"").hex() if isinstance(blob, (bytes, bytearray)) else ""


_FRACTION_RE = re.compile(r"\.(\d{7,})")


def _parse_ts(ts: str | None) -> datetime | None:
    """解析 ISO 时间戳（Zed 用的格式，可能有 7+ 位小数）"""
    if not ts:
        return None
    # 截断超过 6 位的小数部分
    normalized = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6], ts)
    try:
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _parse_paths(raw: str | None) -> list[str]:
    """folder_paths 换行分隔的路径列表"""
    if not raw:
        return []
    return [p for p in raw.split("\n") if p.strip()]


# ========== Zed 数据库访问 ==========

class ZedRepo:
    """只读访问 Zed db.sqlite（sidebar_threads 表）"""

    REQUIRED_COLUMNS = {
        "thread_id",
        "session_id",
        "agent_id",
        "title",
        "title_override",
        "folder_paths",
        "archived",
        "created_at",
        "updated_at",
        "interacted_at",
    }

    def __init__(self, db_path: Path | None = None):
        """
        db_path: Zed db.sqlite 的路径（或其父目录）
        默认使用 default_zed_db_dir() / "db.sqlite"
        """
        self.db_path = db_path or (default_zed_db_dir() / "db.sqlite")

    def list_threads(
        self,
        project: str | None = None,
        agent: str = "opencode",
        archived: bool = False,
    ) -> list[Thread]:
        """
        查询 sidebar_threads，过滤条件：
        - agent_id = "opencode"（可自定义）
        - folder_paths 包含 project 子串
        - archived = 0 (除非 archived=True)

        返回按 updated_at 倒序排列的 Thread 列表
        """
        with open_snapshot(self.db_path) as snap:
            con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row

            # 校验 schema
            self._check_schema(con)

            rows = con.execute(
                """
                SELECT thread_id, session_id, agent_id, title, title_override,
                       folder_paths, archived, created_at, updated_at, interacted_at
                FROM sidebar_threads
                WHERE agent_id = ?
            """,
                (agent,),
            ).fetchall()

            threads = []
            for r in rows:
                # folder_paths 换行分隔
                paths = _parse_paths(r["folder_paths"])

                # project 子串过滤（不区分大小写）
                if project and not any(
                    project.lower() in p.lower() for p in paths
                ):
                    continue

                # archived 过滤
                if r["archived"] and not archived:
                    continue

                threads.append(
                    Thread(
                        id=_blob_to_uuid(r["thread_id"]),
                        session_id=r["session_id"],
                        agent_id=r["agent_id"] or "unknown",
                        title=(r["title_override"] or r["title"] or "").strip(),
                        archived=bool(r["archived"]),
                        projects=paths,
                        created_at=_parse_ts(r["created_at"]),
                        updated_at=_parse_ts(r["updated_at"]),
                        interacted_at=_parse_ts(r["interacted_at"]),
                    )
                )

            con.close()

            # 按 updated_at 倒序排列
            threads.sort(
                key=lambda t: t.updated_at or t.created_at or datetime.min,
                reverse=True,
            )
            return threads

    def get_thread(self, thread_id: str) -> Thread:
        """根据 thread_id 获取单个 Thread"""
        threads = self.list_threads()  # 全量加载再查找（小数据集）
        tid = thread_id.strip().lower()
        for t in threads:
            if t.id.lower() == tid:
                return t
        raise NotFoundError(f"Thread not found: {thread_id}")

    def _check_schema(self, con: sqlite3.Connection) -> None:
        """校验 sidebar_threads 表结构"""
        try:
            rows = con.execute("PRAGMA table_info(sidebar_threads)").fetchall()
        except sqlite3.DatabaseError as exc:
            raise SchemaError(f"Not a readable Zed database: {exc}") from exc

        cols = {r["name"] for r in rows}
        if not cols:
            raise SchemaError("Table sidebar_threads not found")

        missing = self.REQUIRED_COLUMNS - cols
        if missing:
            raise SchemaError(
                f"Zed schema mismatch, missing columns: {', '.join(sorted(missing))}"
            )


# ========== OpenCode 数据库访问 ==========

class OpencodeRepo:
    """只读访问 OpenCode opencode.db（session/message/part 表）"""

    def __init__(self, db_path: Path | None = None):
        """
        db_path: opencode.db 的路径
        默认使用 default_opencode_db_path()
        """
        self.db_path = db_path or default_opencode_db_path()

    def get_session_content(self, session_id: str) -> SessionContent:
        """
        根据 session_id 查询：
        1. session 表元数据
        2. message 表（JOIN part 表）完整对话

        返回 SessionContent 对象
        """
        # 直接只读打开（假设 opencode 未运行）
        uri = f"file:{self.db_path.as_posix()}?mode=ro&immutable=1"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row

        try:
            # 1. 查 session 元数据
            sr = con.execute(
                """
                SELECT id, project_id, directory, title, version, agent, model,
                       time_created, time_archived
                FROM session WHERE id = ?
            """,
                (session_id,),
            ).fetchone()

            if not sr:
                raise NotFoundError(f"Session not found: {session_id}")

            session = SessionMeta(
                id=sr["id"],
                project_id=sr["project_id"],
                directory=sr["directory"],
                title=sr["title"],
                version=sr["version"],
                agent=sr["agent"],
                model=json.loads(sr["model"]) if sr["model"] else None,
                time_created=sr["time_created"],
                time_archived=sr["time_archived"],
            )

            # 2. 查 message 表
            messages_dict: dict[str, Message] = {}

            for mr in con.execute(
                """
                SELECT id, time_created, data FROM message
                WHERE session_id = ? ORDER BY time_created, id
            """,
                (session_id,),
            ):
                mdata = json.loads(mr["data"])
                messages_dict[mr["id"]] = Message(
                    id=mr["id"],
                    role=mdata.get("role", "unknown"),
                    agent=mdata.get("agent"),
                    modelID=mdata.get("modelID"),
                    time_created=mr["time_created"],
                    parts=[],
                )

            # 3. 查 part 表，关联到 message
            for pr in con.execute(
                """
                SELECT id, message_id, time_created, data FROM part
                WHERE session_id = ? ORDER BY time_created, id
            """,
                (session_id,),
            ):
                pdata = json.loads(pr["data"])
                part = MessagePart(
                    id=pr["id"],
                    message_id=pr["message_id"],
                    type=pdata.get("type", "unknown"),
                    text=pdata.get("text", ""),
                    time_created=pr["time_created"],
                )

                if pr["message_id"] in messages_dict:
                    messages_dict[pr["message_id"]].parts.append(part)

            return SessionContent(
                session=session,
                messages=list(messages_dict.values()),
            )

        finally:
            con.close()
