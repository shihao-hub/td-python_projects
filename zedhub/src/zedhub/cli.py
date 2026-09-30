"""zedhub CLI 入口（薄壳）。

架构（v2 daemon 标准）：查询/写命令 = 参数解析 → HTTP → 渲染，本模块
不含业务逻辑；--help/--version/schema 等纯本地命令不触发 daemon。

输出契约：
- 新命令 JSON 走 {"ok": true, "data": ...} / {"ok": false, "error": {...}} 信封；
- 旧兼容命令保留归档基线的 {"status": "ok", ...} 信封；
- 退出码：0 成功（含空结果）、1 运行错误、2 用法错误。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from .buildid import build_id, package_version

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

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


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
            auto_spawned=auto_spawned,
        )
    except Exception as exc:
        typer.secho(f"zedhub: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
