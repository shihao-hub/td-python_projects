"""MCP stdio server for querying Zed + OpenCode sessions.

tool 实现统一在 tools.py（CLI 与 MCP 共用），本模块只负责注册与协议适配。
"""

from __future__ import annotations

from pathlib import Path

from . import tools
from .core.errors import ZocError
from .core.snapshot import SnapshotError


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
            return tools.list_sessions(project, archived, zed_db=zed_db)
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
            return tools.get_session_content(session_id, opencode_db=opencode_db)
        except (ZocError, SnapshotError) as e:
            raise ToolError(f"get_session_content failed: {e}") from e

    return server


def serve(
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
) -> None:
    """MCP stdio server 入口"""
    build_server(zed_db, opencode_db).run(transport="stdio")
