"""MCP stdio server — the same queries exposed as tools for AI clients.

Tools are thin wrappers: their signatures exist only so the MCP server can
derive the JSON Schema; validation and dispatch stay in api.py (single
source of truth shared with ``zedhub rpc``). Tool descriptions are built
from api.PARAM_DOCS at registration time. Each tool call opens a fresh
database snapshot, exactly like every other zedhub entry point.

Client config example (Zed / Claude Code / opencode):
    command: zedhub.exe   args: ["mcp"]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .api import METHOD_SPECS, PARAM_DOCS, ApiError, call
from .core.repo import SchemaError, ZedDb
from .core.service import Service
from .core.snapshot import SnapshotError, open_snapshot


def _call_fresh(method: str, params: dict, db: Path | None) -> Any:
    # 每次调用独立快照:响应永远基于当前数据(与 rpc/CLI 一致)
    from mcp.server.mcpserver.exceptions import ToolError

    try:
        with open_snapshot(db) as snap:
            with ZedDb(snap) as conn:
                return call(method, params, Service(conn))
    except (ApiError, SnapshotError, SchemaError) as exc:
        # ToolError = 预期内失败,message 会透给客户端;裸异常会被 SDK
        # 当作 crash 处理且不透出原因,对 AI 消费方不友好
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc


def _describe(method: str) -> str:
    # 描述文本从 METHOD_SPECS/PARAM_DOCS 拼接,与 RPC/文档保持单一事实源
    spec = METHOD_SPECS[method]
    lines = [spec["summary"]]
    if spec["params"]:
        lines.append("")
        lines.append("Params:")
        for key in spec["params"]:
            lines.append(f"  {key}: {PARAM_DOCS[key]}")
    return "\n".join(lines)


def build_server(db: Path | None = None):
    """Assemble the MCPServer with zedhub's four query tools."""
    # mcp 2.x:FastMCP 已更名 MCPServer;延迟导入避免拖慢普通 CLI 命令
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("zedhub", instructions="Read-only queries over Zed's agent session database.")

    @server.tool(description=_describe("threads.list"))
    def threads_list(
        project: str | None = None,
        agent: str | None = None,
        archived: str = "no",
        since: str | None = None,
        until: str | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """List agent threads, newest first."""
        return _call_fresh(
            "threads.list",
            {"project": project, "agent": agent, "archived": archived, "since": since,
             "until": until, "search": search, "limit": limit},
            db,
        )

    @server.tool(description=_describe("threads.show"))
    def threads_show(thread_id: str) -> dict:
        """Show one thread by uuid."""
        return _call_fresh("threads.show", {"thread_id": thread_id}, db)

    @server.tool(description=_describe("projects"))
    def projects() -> list[dict]:
        """Per-folder project statistics."""
        return _call_fresh("projects", {}, db)

    @server.tool(description=_describe("stats"))
    def stats() -> dict:
        """Global overview: totals, agents, workspace combos, monthly activity."""
        return _call_fresh("stats", {}, db)

    return server


def serve_mcp(db: Path | None = None) -> None:
    build_server(db).run(transport="stdio")
