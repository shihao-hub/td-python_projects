"""MCP stdio server for querying Zed + OpenCode sessions."""

from __future__ import annotations

from pathlib import Path

from .core.errors import NotFoundError, SchemaError, ZocError
from .core.model import OpencodeRepo, ZedRepo
from .core.snapshot import SnapshotError
from .schema import SessionContent, Thread


def build_server(
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
):
    """构建 MCP server，注册两个 tool"""
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer(
        "zed-opencode-sessions",
        instructions="Query Zed agent sessions and retrieve OpenCode conversation content.",
    )

    @server.tool(
        description="List opencode sessions for a given project directory (substring match)."
    )
    def list_sessions(
        project: str,
        archived: bool = False,
    ) -> list[dict]:
        """
        列出指定项目目录的 opencode 会话摘要。

        Args:
            project: 项目路径子串（不区分大小写）
            archived: 是否包含已归档会话

        Returns:
            会话摘要列表，每项包含 thread_id, session_id, title, updated_at 等
        """
        try:
            repo = ZedRepo(zed_db)
            threads = repo.list_threads(
                project=project, agent="opencode", archived=archived
            )

            # 转 JSON-ready dict
            return [_thread_to_dict(t) for t in threads]
        except (ZocError, SnapshotError) as e:
            raise ToolError(f"list_sessions failed: {e}") from e

    @server.tool(
        description="Retrieve full conversation content for a given session_id from OpenCode."
    )
    def get_session_content(session_id: str) -> dict:
        """
        根据 session_id 从 OpenCode 取完整对话内容。

        Args:
            session_id: 从 list_sessions 获得的 session_id

        Returns:
            完整会话内容，包含 session 元数据和 messages 列表
        """
        try:
            repo = OpencodeRepo(opencode_db)
            content = repo.get_session_content(session_id)

            # 转 JSON-ready dict
            return _session_content_to_dict(content)
        except (ZocError, SnapshotError) as e:
            raise ToolError(f"get_session_content failed: {e}") from e

    return server


def serve(
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
) -> None:
    """MCP stdio server 入口"""
    build_server(zed_db, opencode_db).run(transport="stdio")


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
