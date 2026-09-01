from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from zedhub.cli import app
from zedhub.core.model import parse_paths, parse_ts
from zedhub.core.repo import SchemaError, ZedDb
from zedhub.core.service import Service

from .fixture import insert, make_db

runner = CliRunner()


def build_service(tmp_path: Path) -> tuple[Service, sqlite3.Connection]:
    db_path = make_db(tmp_path)
    con = sqlite3.connect(db_path)
    return Service(ZedDb(db_path)), con


# -- model -----------------------------------------------------------------


def test_parse_ts_handles_7_digit_fraction():
    dt = parse_ts("2026-09-01T09:30:29.605964800+00:00")
    assert dt is not None and dt.microsecond == 605964


def test_parse_ts_garbage_returns_none():
    assert parse_ts("not-a-date") is None
    assert parse_ts(None) is None


def test_parse_paths_splits_newlines():
    assert parse_paths("C:\\a\nC:\\b\n") == ["C:\\a", "C:\\b"]
    assert parse_paths(None) == []


# -- repo ------------------------------------------------------------------


def test_schema_error_on_missing_table(tmp_path):
    p = tmp_path / "empty.sqlite"
    sqlite3.connect(p).close()
    with pytest.raises(SchemaError):
        ZedDb(p)


# -- service ---------------------------------------------------------------


def test_title_override_wins(tmp_path):
    svc, con = build_service(tmp_path)
    tid = insert(con, title="raw", override=" pretty title ")
    con.commit()
    assert svc.get_thread(tid).title == "pretty title"


def test_filters(tmp_path):
    svc, con = build_service(tmp_path)
    a1 = insert(con, agent="opencode", title="fix oauth", paths="C:\\proj\\alpha")
    insert(con, agent="codex-acp", title="other", paths="C:\\proj\\beta", archived=1)
    combo = insert(con, title="dual", paths="C:\\proj\\alpha\nC:\\proj\\beta")
    old = insert(con, title="stale", updated="2026-01-01T00:00:00+00:00")
    con.commit()

    assert [t.id for t in svc.list_threads()] == [a1, combo, old]  # newest first, archived hidden

    assert [t.id for t in svc.list_threads(archived="all")] and len(
        svc.list_threads(archived="all")
    ) == 4
    assert len(svc.list_threads(archived="only")) == 1

    assert [t.id for t in svc.list_threads(agent="codex-acp")] == []
    assert len(svc.list_threads(agent="codex-acp", archived="all")) == 1

    assert len(svc.list_threads(project="alpha", archived="all")) == 3  # a1 + combo + old(default alpha)
    assert len(svc.list_threads(project="beta", archived="all")) == 2

    assert len(svc.list_threads(search="OAUTH")) == 1
    from datetime import datetime as _dt
    assert len(svc.list_threads(since=_dt.fromisoformat("2026-08-01T00:00:00"))) == 2  # old excluded
    assert len(svc.list_threads(limit=1)) == 1


def test_projects_and_stats(tmp_path):
    svc, con = build_service(tmp_path)
    insert(con, paths="C:\\proj\\alpha")
    insert(con, paths="C:\\proj\\alpha", archived=1, agent="codex-acp")
    insert(con, paths="C:\\proj\\alpha\nC:\\proj\\beta")
    con.commit()

    projects = {p.path: p for p in svc.projects()}
    assert projects["C:\\proj\\alpha"].total == 3
    assert projects["C:\\proj\\alpha"].archived == 1
    assert projects["C:\\proj\\alpha"].agents == {"opencode": 2, "codex-acp": 1}
    assert projects["C:\\proj\\beta"].total == 1

    stats = svc.stats()
    assert stats.total_threads == 3
    assert stats.active == 2 and stats.archived == 1
    assert stats.projects == 2
    assert len(stats.workspace_combos) == 2
    assert sum(stats.monthly.values()) == 3


# -- cli -------------------------------------------------------------------


def cli(*args, db: Path) -> object:
    result = runner.invoke(app, [*args, "--db", str(db)])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_cli_envelope_and_show(tmp_path):
    db_path = make_db(tmp_path)
    con = sqlite3.connect(db_path)
    tid = insert(con, title="envelope check")
    con.commit()

    out = cli("threads", "list", db=db_path)
    assert out["status"] == "ok"
    assert out["count"] == 1
    assert out["elapsed_ms"] >= 0
    assert out["data"][0]["id"] == tid
    assert "projects" in out["data"][0]

    shown = cli("threads", "show", tid, db=db_path)
    assert shown["data"]["title"] == "envelope check"


def test_cli_export_html(tmp_path):
    db_path = make_db(tmp_path)
    con = sqlite3.connect(db_path)
    insert(con, title="</script><b>evil</b>")
    con.commit()

    out_file = tmp_path / "demo.html"
    result = runner.invoke(app, ["export", "--db", str(db_path), "--out", str(out_file)])
    assert result.exit_code == 0, result.output
    html = out_file.read_text(encoding="utf-8")
    assert "zedhub" in html
    assert "</script><b>evil</b>" not in html  # escaped breakout
