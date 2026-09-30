"""zedhub CLI 入口（薄壳）。

架构（v2 daemon 标准）：查询/写命令 = 参数解析 → HTTP → 渲染，本模块
不含业务逻辑；--help/--version/schema 等纯本地命令不触发 daemon。

信封契约：
- 旧兼容命令（threads/projects/stats）：归档基线 {"status":"ok","data":...,
  "count":...,"elapsed_ms":...}，默认 JSON、--table 人读（FR-9 不变）；
- 新命令（sessions/stats effort/archive）：{"ok":true,"data":...} /
  {"ok":false,"error":{...}}，默认人读、--json 输出（v1 标准）；
- 退出码：0 成功（含空结果）、1 运行错误、2 用法错误。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Annotated, Optional

import typer

from .buildid import build_id, package_version
from .client import DaemonClient
from .contract import API_PREFIX
from .core.errors import EXIT_CODE, ZedhubError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

app = typer.Typer(
    help=(
        "Zed + OpenCode agent session hub (daemon architecture). "
        "Query/write commands talk to the local daemon (`zedhub serve`); "
        "--help/--version/schema are fully local."
    ),
    no_args_is_help=True,
    add_completion=False,
)
threads_app = typer.Typer(help="List and inspect Zed threads.", no_args_is_help=True)
app.add_typer(threads_app, name="threads")
sessions_app = typer.Typer(help="List and inspect agent sessions (OpenCode).", no_args_is_help=True)
app.add_typer(sessions_app, name="sessions")
stats_app = typer.Typer(help="Zed overview and OpenCode effort stats.", invoke_without_command=True)
app.add_typer(stats_app, name="stats")

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

HOST_OPT = Annotated[
    Optional[str],
    typer.Option("--host", help="Explicit daemon address host:port (loopback only)."),
]
JSON_OPT = Annotated[bool, typer.Option("--json", help="Stable JSON envelope on stdout.")]
TABLE_OPT = Annotated[bool, typer.Option("--table", help="Human-readable table instead of JSON (legacy commands).")]


# -- 通用输出辅助 ---------------------------------------------------------------


def _die(msg: str, code: int = 1) -> None:
    typer.secho(f"zedhub: {msg}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=code)


def _fail(exc: ZedhubError, *, as_json: bool) -> None:
    """统一失败路径：--json 走 stdout 单对象，人读走 stderr。"""
    if as_json:
        json.dump({"ok": False, "error": exc.to_payload()}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    raise typer.Exit(code=EXIT_CODE.get(exc.code, 1))


def _emit_legacy(data, started: float, *, table: bool = False, renderer=None) -> None:
    """旧兼容信封（归档基线）：默认 JSON、--table 人读。"""
    if table and renderer is not None:
        renderer(data)
        return
    envelope = {
        "status": "ok",
        "data": data,
        "count": len(data) if isinstance(data, list) else 1,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    json.dump(envelope, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def _emit_new(data, *, as_json: bool, renderer=None) -> None:
    """新命令信封：默认人读、--json 输出。"""
    if as_json:
        json.dump({"ok": True, "data": data}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    elif renderer is not None:
        renderer(data)


def _run_query(fn, *, as_json: bool):
    """执行一次 HTTP 查询并统一错误路径。"""
    started = time.perf_counter()
    try:
        return fn(), started
    except ZedhubError as exc:
        if not as_json:
            _die(str(exc), EXIT_CODE.get(exc.code, 1))
        _fail(exc, as_json=True)


def _version_callback(value: bool) -> None:
    # 纯本地命令：零数据库访问、零网络连接
    if value:
        typer.echo(f"zedhub {package_version()} ({build_id()})")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and build id, then exit (fully local).",
    ),
) -> None:
    """zedhub — unified Zed + OpenCode session tooling."""


# -- daemon ------------------------------------------------------------------


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Loopback bind address (127.0.0.1/localhost/::1).")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="HTTP API port.")] = 8766,
    ws_port: Annotated[int, typer.Option("--ws-port", help="WebSocket channel port (0 disables). Frozen learning channel; use HTTP for automation.")] = 8765,
    db: Annotated[Optional[Path], typer.Option("--db", help="Zed db.sqlite (or its dir). Defaults to Zed's live location.")] = None,
    opencode_db: Annotated[Optional[Path], typer.Option("--opencode-db", help="opencode.db path. Defaults to auto-detect.")] = None,
    auto_spawned: Annotated[bool, typer.Option("--auto-spawned", help="(internal) mark daemon as auto-spawned.", hidden=True)] = False,
) -> None:
    """Run the daemon (the only business process). Ctrl+C stops it."""
    if host.strip("[]") not in LOOPBACK_HOSTS:
        typer.secho(
            f"zedhub: --host must be a loopback address (127.0.0.1/localhost/::1), got: {host}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    from .lifecycle import serve_with_lifecycle

    try:
        serve_with_lifecycle(
            host=host, port=port, zed_db=db, opencode_db=opencode_db,
            auto_spawned=auto_spawned, ws_port=ws_port,
        )
    except Exception as exc:
        typer.secho(f"zedhub: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


# -- threads（旧兼容命令：默认 JSON、--table 人读） ----------------------------


@threads_app.command("list")
def threads_list(
    project: Annotated[Optional[str], typer.Option("--project", help="Substring match on project path.")] = None,
    agent: Annotated[Optional[str], typer.Option("--agent", help="Exact agent id, e.g. opencode.")] = None,
    archived: Annotated[str, typer.Option("--archived", help="no=active only (default), only=archived, all.")] = "no",
    since: Annotated[Optional[str], typer.Option("--since", help="YYYY-MM-DD or ISO datetime.")] = None,
    until: Annotated[Optional[str], typer.Option("--until", help="YYYY-MM-DD or ISO datetime.")] = None,
    search: Annotated[Optional[str], typer.Option("--search", help="Case-insensitive substring in title/agent/id.")] = None,
    limit: Annotated[int, typer.Option("--limit", min=0, help="Cap results; 0 means no cap.")] = 0,
    host: HOST_OPT = None,
    table: TABLE_OPT = False,
) -> None:
    """List threads, newest first."""
    from .serialization import render_thread_table

    def fn():
        return DaemonClient(host).call(
            "GET", f"{API_PREFIX}/threads",
            query={"project": project, "agent": agent, "archived": archived,
                   "since": since, "until": until, "search": search,
                   "limit": limit or None},
        )

    data, started = _run_query(fn, as_json=False)
    _emit_legacy(data, started, table=table, renderer=render_thread_table)


@threads_app.command("show")
def threads_show(
    thread_id: Annotated[str, typer.Argument(help="Thread uuid (see threads list).")],
    host: HOST_OPT = None,
) -> None:
    """Show one thread by uuid."""
    def fn():
        return DaemonClient(host).call("GET", f"{API_PREFIX}/threads/{thread_id}")

    data, started = _run_query(fn, as_json=False)
    _emit_legacy(data, started)


@app.command()
def projects(
    host: HOST_OPT = None,
    table: TABLE_OPT = False,
) -> None:
    """Per-folder project statistics."""
    from .serialization import render_project_table

    def fn():
        return DaemonClient(host).call("GET", f"{API_PREFIX}/projects")

    data, started = _run_query(fn, as_json=False)
    _emit_legacy(data, started, table=table, renderer=render_project_table)


@stats_app.callback()
def stats_cmd(
    ctx: typer.Context,
    host: HOST_OPT = None,
) -> None:
    """Global overview: totals, agents, workspace combos, monthly activity."""
    if ctx.invoked_subcommand is not None:
        return

    def fn():
        return DaemonClient(host).call("GET", f"{API_PREFIX}/stats")

    data, started = _run_query(fn, as_json=False)
    _emit_legacy(data, started)


@stats_app.command("effort")
def stats_effort(
    host: HOST_OPT = None,
    json_out: JSON_OPT = False,
    watch: Annotated[bool, typer.Option("--watch", help="Refresh repeatedly (ANSI clear).")] = False,
    interval: Annotated[float, typer.Option("-i", "--interval", min=0.1, help="Watch interval seconds.")] = 5.0,
) -> None:
    """OpenCode startup model × effort report (ocstat format)."""
    from .serialization import render_effort_report

    if watch and interval <= 0:
        _die("invalid interval (must be > 0)", code=2)

    def fn():
        return DaemonClient(host).call("GET", f"{API_PREFIX}/stats/effort", timeout=60.0)

    try:
        while True:
            data, _ = _run_query(fn, as_json=json_out)
            if watch:
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
            _emit_new(data, as_json=json_out, renderer=render_effort_report)
            if not watch:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        raise typer.Exit() from None


# -- programmatic protocols ----------------------------------------------------


@app.command("rpc")
def rpc_cmd() -> None:
    """JSON-RPC 2.0 over line-delimited stdio (frozen compatibility shell).

    rpc.discover is answered locally (no daemon); the four business methods
    are forwarded to the daemon over HTTP.
    """
    from .rpc import serve

    serve()


@app.command("mcp")
def mcp_cmd() -> None:
    """MCP bridge: stdio(MCP) <-> HTTP daemon (read-only tools only)."""
    from .mcp_bridge import serve_mcp

    serve_mcp()


# -- sessions（新命令：默认人读、--json） ---------------------------------------


@sessions_app.command("list")
def sessions_list(
    project: Annotated[Optional[str], typer.Option("--project", help="Substring match on session directory.")] = None,
    agent: Annotated[Optional[str], typer.Option("--agent", help="Exact agent filter.")] = None,
    archived: Annotated[str, typer.Option("--archived", help="no=active only (default), only=archived, all.")] = "no",
    limit: Annotated[int, typer.Option("--limit", min=0, help="Cap results; 0 means no cap.")] = 0,
    source: Annotated[str, typer.Option("--source", help="Agent source id (only opencode implemented).")] = "opencode",
    host: HOST_OPT = None,
    json_out: JSON_OPT = False,
) -> None:
    """List agent sessions, newest first (with zed_linked marker)."""
    from .serialization import render_session_table

    def fn():
        return DaemonClient(host).call(
            "GET", f"{API_PREFIX}/sessions",
            query={"source": source, "project": project, "agent": agent,
                   "archived": archived, "limit": limit or None},
        )

    data, _ = _run_query(fn, as_json=json_out)
    _emit_new(data, as_json=json_out, renderer=render_session_table)


@sessions_app.command("show")
def sessions_show(
    session_id: Annotated[str, typer.Argument(help="Session id (see sessions list).")],
    source: Annotated[str, typer.Option("--source", help="Agent source id.")] = "opencode",
    host: HOST_OPT = None,
    json_out: JSON_OPT = False,
) -> None:
    """Show one session by id."""
    from .serialization import render_session_detail

    def fn():
        return DaemonClient(host).call(
            "GET", f"{API_PREFIX}/sessions/{session_id}", query={"source": source}
        )

    data, _ = _run_query(fn, as_json=json_out)
    _emit_new(data, as_json=json_out, renderer=render_session_detail)


@sessions_app.command("content")
def sessions_content(
    session_id: Annotated[str, typer.Argument(help="Session id (see sessions list).")],
    source: Annotated[str, typer.Option("--source", help="Agent source id.")] = "opencode",
    fmt: Annotated[str, typer.Option("--format", help="text | markdown")] = "text",
    out: Annotated[Optional[Path], typer.Option("--out", help="Write to file instead of stdout.")] = None,
    host: HOST_OPT = None,
    json_out: JSON_OPT = False,
) -> None:
    """Full session content: session meta + messages + parts."""
    from .export.markdown import render_markdown, write_markdown
    from .serialization import render_content_text

    def fn():
        return DaemonClient(host).call(
            "GET", f"{API_PREFIX}/sessions/{session_id}/content", query={"source": source}
        )

    data, _ = _run_query(fn, as_json=json_out)
    if json_out:
        _emit_new(data, as_json=True)
        return
    if fmt == "markdown":
        if out is not None:
            write_markdown(data, out)
            print(f"wrote {out.resolve()}")
        else:
            print(render_markdown(data))
    elif fmt == "text":
        if out is not None:
            import io

            buf = io.StringIO()
            _stdout, sys.stdout = sys.stdout, buf
            try:
                render_content_text(data)
            finally:
                sys.stdout = _stdout
            out.write_text(buf.getvalue(), encoding="utf-8")
            print(f"wrote {out.resolve()}")
        else:
            render_content_text(data)
    else:
        _die(f"invalid --format: {fmt} (use text | markdown)", code=2)
