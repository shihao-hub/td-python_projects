"""MCP tool 实现：list_sessions / get_session_content。

mcp_server.py 将其注册为 MCP tool；cli.py 与 scripts 直接调用同一批函数。
"""

from __future__ import annotations

from pathlib import Path

from .core.model import OpencodeRepo, ZedRepo
from .schema import SessionContent, Thread


def list_sessions(
    project: str,
    archived: bool = False,
    *,
    zed_db: Path | None = None,
) -> list[dict]:
    """
    列出指定项目目录的 opencode 会话摘要。

    Args:
        project: 项目路径子串（不区分大小写）
        archived: 是否包含已归档会话
        zed_db: Zed db.sqlite 路径（默认自动探测）

    Returns:
        会话摘要列表，每项包含 thread_id, session_id, title, updated_at 等
    """
    repo = ZedRepo(zed_db)
    threads = repo.list_threads(project=project, agent="opencode", archived=archived)
    return [_thread_to_dict(t) for t in threads]


def get_session_content(
    session_id: str,
    *,
    opencode_db: Path | None = None,
) -> dict:
    """
    根据 session_id 从 OpenCode 取完整对话内容。

    Args:
        session_id: 从 list_sessions 获得的 session_id
        opencode_db: OpenCode opencode.db 路径（默认自动探测）

    Returns:
        完整会话内容，包含 session 元数据和 messages 列表
    """
    repo = OpencodeRepo(opencode_db)
    content = repo.get_session_content(session_id)
    return _session_content_to_dict(content)


# ========== 序列化辅助函数 ==========


def _thread_to_dict(t: Thread) -> dict:
    """Thread 对象转 JSON-ready dict"""
    return {
        "thread_id": t.id,
        "session_id": t.session_id,
        "title": t.title,
        "projects": t.projects,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "archived": t.archived,
    }


def _session_content_to_dict(content: SessionContent) -> dict:
    """SessionContent 对象转 JSON-ready dict"""
    return {
        "session": {
            "id": content.session.id,
            "title": content.session.title,
            "directory": content.session.directory,
            "version": content.session.version,
            "agent": content.session.agent,
            "model": content.session.model,
            "time_created": content.session.time_created,
        },
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "agent": msg.agent,
                "modelID": msg.modelID,
                "time_created": msg.time_created,
                "parts": [
                    {
                        "id": part.id,
                        "type": part.type,
                        "text": part.text,
                        "time_created": part.time_created,
                    }
                    for part in msg.parts
                ],
            }
            for msg in content.messages
        ],
    }
