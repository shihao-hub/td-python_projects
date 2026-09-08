"""Typer CLI 入口：list/show/mcp 命令。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from .core.errors import NotFoundError, SchemaError, ZocError
from .core.model import OpencodeRepo, ZedRepo
from .core.snapshot import SnapshotError
from .mcp_server import _session_content_to_dict, _thread_to_dict

# Windows 控制台 UTF-8 输出（避免中文乱码）
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

app = typer.Typer(
    help="Query Zed + OpenCode session data",
    no_args_is_help=True,
)

# 通用选项
ZedDbOpt = Annotated[
    Optional[Path],
    typer.Option("--zed-db", help="Zed db.sqlite 路径（默认自动探测）"),
]
OpencodeDbOpt = Annotated[
    Optional[Path],
    typer.Option("--opencode-db", help="OpenCode opencode.db 路径（默认自动探测）"),
]


@app.command("list")
def list_sessions(
    project: Annotated[str, typer.Argument(help="项目路径子串（不区分大小写）")],
    archived: Annotated[bool, typer.Option("--archived", help="包含已归档会话")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON 格式")] = False,
    zed_db: ZedDbOpt = None,
) -> None:
    """列出指定项目目录的 opencode 会话摘要"""
    try:
        repo = ZedRepo(zed_db)
        threads = repo.list_threads(project=project, agent="opencode", archived=archived)

        if json_output:
            # JSON 输出
            data = [_thread_to_dict(t) for t in threads]
            json.dump({"status": "ok", "count": len(data), "data": data}, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            # 表格输出
            if not threads:
                print("(no sessions found)")
                return

            print(f"{'TITLE':<50}  {'SESSION_ID':<30}  {'UPDATED':<20}  PROJECTS")
            print("-" * 120)
            for t in threads:
                title = (t.title or "(untitled)")[:48]
                session_id = (t.session_id or "N/A")[:28]
                updated = t.updated_at.strftime("%Y-%m-%d %H:%M:%S") if t.updated_at else "N/A"
                projects = ", ".join(Path(p).name for p in t.projects) or "N/A"
                print(f"{title:<50}  {session_id:<30}  {updated:<20}  {projects}")

            print(f"\n{len(threads)} session(s)")

    except (ZocError, SnapshotError) as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1)


@app.command("show")
def show_session(
    session_id: Annotated[str, typer.Argument(help="Session ID")],
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON 格式")] = False,
    opencode_db: OpencodeDbOpt = None,
) -> None:
    """显示会话详情（完整对话内容）"""
    try:
        repo = OpencodeRepo(opencode_db)
        content = repo.get_session_content(session_id)

        if json_output:
            # JSON 输出
            data = _session_content_to_dict(content)
            json.dump({"status": "ok", "data": data}, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            # 人类可读格式
            s = content.session
            print(f"{'='*80}")
            print(f"Session: {s.title}")
            print(f"ID: {s.id}")
            print(f"Directory: {s.directory}")
            print(f"Agent: {s.agent or 'N/A'}")
            print(f"Model: {json.dumps(s.model, ensure_ascii=False) if s.model else 'N/A'}")
            print(f"Version: {s.version}")
            print(f"Created: {_format_timestamp(s.time_created)}")
            print(f"{'='*80}\n")

            for i, msg in enumerate(content.messages, 1):
                # 不用 emoji：Windows 控制台默认 GBK，emoji 会直接 UnicodeEncodeError
                print(f"[{i}] {msg.role.upper()}")
                print(f"    Time: {_format_timestamp(msg.time_created)}")
                print(f"    Agent: {msg.agent or 'N/A'} | Model: {msg.modelID or 'N/A'}")

                # 只显示 type=text 的 part
                text_parts = [p for p in msg.parts if p.type == "text" and p.text.strip()]
                if text_parts:
                    print(f"    Content:")
                    for part in text_parts:
                        # 缩进显示内容
                        for line in part.text.splitlines():
                            print(f"      {line}")
                else:
                    print(f"    (no text content)")

                print()

    except (ZocError, SnapshotError) as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1)


@app.command("mcp")
def mcp_server(
    zed_db: ZedDbOpt = None,
    opencode_db: OpencodeDbOpt = None,
) -> None:
    """启动 MCP stdio server（供 AI 客户端调用）"""
    from .mcp_server import serve

    try:
        serve(zed_db, opencode_db)
    except Exception as e:
        print(f"MCP server error: {e}", file=sys.stderr)
        raise typer.Exit(code=1)


def _format_timestamp(ts_ms: int) -> str:
    """毫秒时间戳转本地时间字符串"""
    from datetime import datetime

    dt = datetime.fromtimestamp(ts_ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    app()
