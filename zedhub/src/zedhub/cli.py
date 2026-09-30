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
