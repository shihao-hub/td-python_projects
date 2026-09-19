"""zedhub — read-only CLI over Zed's agent session database.

Output contract (what frontends/other tools may rely on):
- Successful commands print a single JSON object to stdout:
    {"status": "ok", "data": <list|object>, "count": <int>, "elapsed_ms": <int>}
  unless --table is given (human-readable, unstable format).
- datetimes are local-time ISO strings.
- Exit codes: 0 ok (empty results included), 1 runtime error, 2 usage error.
- Errors go to stderr as plain text; nothing is printed to stdout on failure.

Programmatic access (standard protocols, no custom envelope knowledge needed):
- `zedhub rpc`   JSON-RPC 2.0 over line-delimited stdio (one request per
                 line, one response per line). Method table and error codes:
                 docs/projects/python_projects/zedhub/protocol.md.
- `zedhub mcp`   MCP stdio server exposing the same queries as tools.
Both share the method registry in api.py; payload shapes are identical to
the "data" field of the JSON envelope above.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer

from .api import dump_projects, dump_threads
from .core.model import Overview, Thread
from .core.repo import SchemaError, ZedDb
from .core.service import ArchivedFilter, NotFoundError, Service
from .core.snapshot import SnapshotError, open_snapshot
from .export.demo import render_demo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

app = typer.Typer(
    help="Read-only queries over Zed's agent session database.",
    no_args_is_help=True,
    add_completion=False,
)
threads_app = typer.Typer(help="List and inspect agent threads.", no_args_is_help=True)
app.add_typer(threads_app, name="threads")

DB_OPT = Annotated[
    Optional[Path],
    typer.Option("--db", help="Path to db.sqlite or its 0-stable dir. Defaults to Zed's live location."),
]
TABLE_OPT = Annotated[bool, typer.Option("--table", help="Human-readable table instead of JSON.")]
ARCHIVED_OPT = Annotated[
    ArchivedFilter,
    typer.Option("--archived", help="no=active only (default), only=archived, all."),
]


def _die(msg: str) -> None:
    typer.secho(f"zedhub: {msg}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _parse_date(value: str, flag: str) -> datetime:
    for candidate in (value, value + "T00:00:00"):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    _die(f"invalid date for {flag}: {value} (use YYYY-MM-DD or ISO datetime)")


def _localize_threads(items: list[Thread]) -> list[dict]:
    # 本地时区序列化的唯一实现在 api.py,CLI 信封与 RPC/MCP 共用
    return dump_threads(items)


def _run(fn, db: Path | None = None, table: bool = False) -> None:
    """Run fn(service) -> (data, renderer|None) inside a snapshot; emit envelope."""
    started = time.perf_counter()
    try:
        # sh-todo: 确认一下真的要每次都复制吗？不能加个时延吗？
        with open_snapshot(db) as snap:
            with ZedDb(snap) as db:
                data, renderer = fn(Service(db))
    except (SnapshotError, SchemaError) as exc:
        _die(str(exc))
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


# -- threads ---------------------------------------------------------------


@threads_app.command("list")
def threads_list(
    project: Annotated[Optional[str], typer.Option("--project", help="Substring match on project path.")] = None,
    agent: Annotated[Optional[str], typer.Option("--agent", help="Exact agent id, e.g. opencode.")] = None,
    archived: ARCHIVED_OPT = "no",
    since: Annotated[Optional[str], typer.Option("--since", help="YYYY-MM-DD or ISO datetime.")] = None,
    until: Annotated[Optional[str], typer.Option("--until", help="YYYY-MM-DD or ISO datetime.")] = None,
    search: Annotated[Optional[str], typer.Option("--search", help="Case-insensitive substring in title/agent/id.")] = None,
    limit: Annotated[int, typer.Option("--limit", min=0, help="Cap results; 0 means no cap.")] = 0,
    db: DB_OPT = None,
    table: TABLE_OPT = False,
) -> None:
    """List threads, newest first."""

    def run(svc: Service):
        items = svc.list_threads(
            project=project,
            agent=agent,
            archived=archived,
            since=_parse_date(since, "--since") if since else None,
            until=_parse_date(until, "--until") if until else None,
            search=search,
            limit=limit or None,
        )
        return _localize_threads(items), _render_thread_table

    _run(run, db, table)


@threads_app.command("show")
def threads_show(
    thread_id: Annotated[str, typer.Argument(help="Thread uuid (see threads list).")],
    db: DB_OPT = None,
) -> None:
    """Show one thread by uuid."""

    def run(svc: Service):
        try:
            t = svc.get_thread(thread_id)
        except NotFoundError as exc:
            _die(str(exc))
        return _localize_threads([t])[0], None

    _run(run, db)


# -- aggregates ------------------------------------------------------------


def _fmt_dt(iso: str | None) -> str:
    return iso[:16].replace("T", " ") if iso else "-"


def _render_thread_table(items: list[dict]) -> None:
    if not items:
        print("(no threads)")
        return
    w_title, w_agent = 56, 12
    print(f"{'TITLE':<{w_title}}  {'AGENT':<{w_agent}}  {'ARCH':<4}  {'UPDATED':<16}  PROJECTS")
    for t in items:
        title = (t["title"] or "(untitled)")[: w_title - 1]
        projects = ", ".join(Path(p).name for p in t["projects"])
        print(
            f"{title:<{w_title}}  {t['agent_id'][:w_agent]:<{w_agent}}"
            f"  {'*' if t['archived'] else '':<4}  {_fmt_dt(t['updated_at']):<16}  {projects}"
        )
    print(f"\n{len(items)} thread(s)")


@app.command()
def projects(db: DB_OPT = None, table: TABLE_OPT = False) -> None:
    """Per-folder project statistics."""

    def run(svc: Service):
        items = svc.projects()

        def render(items: list[dict]):
            if not items:
                print("(no projects)")
                return
            print(f"{'ACTIVE':>6}  {'ARCH':<4}  {'LAST ACTIVITY':<16}  PATH")
            for p in items:
                print(f"{p['active']:>6}  {p['archived']:>4}  {_fmt_dt(p['last_activity']):<16}  {p['path']}")

        return dump_projects(items), render

    _run(run, db, table)


@app.command()
def stats(db: DB_OPT = None) -> None:
    """Global overview: totals, agents, workspace combos, monthly activity."""

    def run(svc: Service):
        ov: Overview = svc.stats()
        return ov.model_dump(mode="json"), None

    _run(run, db)


# -- programmatic protocols -------------------------------------------------


@app.command("rpc")
def rpc_cmd(db: DB_OPT = None) -> None:
    """JSON-RPC 2.0 over line-delimited stdio (one request per line).

    Single piped line = one-shot call (EOF ends the process); multiple
    lines = long-lived session. Each request snapshots the database fresh.
    """
    from .rpc import serve

    serve(db)


@app.command("mcp")
def mcp_cmd(db: DB_OPT = None) -> None:
    """MCP stdio server exposing the queries as tools (for AI clients)."""
    from .mcp_server import serve_mcp

    serve_mcp(db)


# -- export ----------------------------------------------------------------


@app.command("export")
def export_html(
    out: Annotated[Optional[Path], typer.Option("--out", help="Output html file.")] = None,
    db: DB_OPT = None,
) -> None:
    """Generate a self-contained HTML demo (data embedded, zero backend)."""
    out_path = out or Path("zedhub-demo.html")

    def run(svc: Service):
        threads = svc.list_threads(archived="all")

        def render(payload: dict):
            html = render_demo(json.dumps(payload, ensure_ascii=False))
            out_path.write_text(html, encoding="utf-8")
            print(f"wrote {out_path.resolve()} ({len(payload['threads'])} threads)")

        payload = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "threads": _localize_threads(threads),
            "projects": dump_projects(svc.projects()),
            "stats": svc.stats().model_dump(mode="json"),
        }
        return payload, render

    _run(run, db, table=True)


@app.command("export-cli")
def export_cli(
    out: Annotated[Optional[Path], typer.Option("--out", help="Output html file.")] = None,
    db: DB_OPT = None,
) -> None:
    """Same as export, but collects data by spawning `uv run zedhub` subprocesses."""

    def fetch(args: list[str]):
        cmd = ["uv", "run", "zedhub", *args]
        if db:
            cmd += ["--db", str(db)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            _die(f"subprocess failed ({' '.join(args)}): {proc.stderr.strip()}")
        return json.loads(proc.stdout)["data"]

    threads = fetch(["threads", "list", "--archived", "all"])
    projects = fetch(["projects"])
    stats = fetch(["stats"])
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "threads": threads,
        "projects": projects,
        "stats": stats,
    }
    out_path = out or Path("zedhub-demo-cli.html")
    out_path.write_text(render_demo(json.dumps(payload, ensure_ascii=False)), encoding="utf-8")
    print(f"wrote {out_path.resolve()} ({len(threads)} threads)")
