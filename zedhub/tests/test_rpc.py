"""JSON-RPC 2.0 front: dispatch, envelope, error mapping, one-shot loop."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from zedhub.cli import app
from zedhub.rpc import handle_line

from .fixture import insert, make_db

runner = CliRunner()


def build_db(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    db_path = make_db(tmp_path)
    con = sqlite3.connect(db_path)
    return db_path, con


def rpc(line: str, db: Path) -> dict:
    return handle_line(line, db)


def test_threads_list_ok(tmp_path):
    db, con = build_db(tmp_path)
    tid = insert(con, title="rpc check")
    con.commit()

    resp = rpc('{"jsonrpc": "2.0", "id": 1, "method": "threads.list", "params": {}}', db)
    assert resp["jsonrpc"] == "2.0" and resp["id"] == 1
    result = resp["result"]
    assert result["count"] == 1
    assert result["data"][0]["id"] == tid
    assert result["elapsed_ms"] >= 0


def test_threads_list_filters(tmp_path):
    db, con = build_db(tmp_path)
    insert(con, agent="opencode", title="fix oauth")
    insert(con, agent="codex-acp", title="other", archived=1)
    con.commit()

    req = json.dumps({"jsonrpc": "2.0", "id": "a", "method": "threads.list", "params": {"archived": "all", "agent": "codex-acp"}})
    resp = rpc(req, db)
    assert resp["id"] == "a"
    assert resp["result"]["count"] == 1
    assert resp["result"]["data"][0]["agent_id"] == "codex-acp"


def test_threads_show_and_not_found(tmp_path):
    db, con = build_db(tmp_path)
    tid = insert(con, title="show me")
    con.commit()

    ok = rpc(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "threads.show", "params": {"thread_id": tid}}), db)
    assert ok["result"]["data"]["title"] == "show me"

    miss = rpc(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "threads.show", "params": {"thread_id": "00000000-0000-0000-0000-000000000000"}}), db)
    assert miss["error"]["code"] == -32001


def test_projects_and_stats(tmp_path):
    db, con = build_db(tmp_path)
    insert(con, paths="C:\\proj\\alpha")
    insert(con, paths="C:\\proj\\alpha\nC:\\proj\\beta")
    con.commit()

    projects = rpc(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "projects"}), db)
    assert projects["result"]["count"] == 2

    stats = rpc(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "stats"}), db)
    assert stats["result"]["data"]["total_threads"] == 2
    assert stats["result"]["data"]["projects"] == 2


def test_method_not_found(tmp_path):
    db, _ = build_db(tmp_path)
    resp = rpc(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "nope.nope"}), db)
    assert resp["error"]["code"] == -32601
    assert "threads.list" in resp["error"]["message"]


def test_invalid_params(tmp_path):
    db, con = build_db(tmp_path)
    insert(con)
    con.commit()

    bad_archived = rpc(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "threads.list", "params": {"archived": "maybe"}}), db)
    assert bad_archived["error"]["code"] == -32602

    bad_limit = rpc(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "threads.list", "params": {"limit": "5"}}), db)
    assert bad_limit["error"]["code"] == -32602

    bad_since = rpc(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "threads.list", "params": {"since": "not-a-date"}}), db)
    assert bad_since["error"]["code"] == -32602

    missing_tid = rpc(json.dumps({"jsonrpc": "2.0", "id": 4, "method": "threads.show", "params": {}}), db)
    assert missing_tid["error"]["code"] == -32602


def test_parse_error(tmp_path):
    db, _ = build_db(tmp_path)
    resp = rpc("{not json", db)
    assert resp["error"]["code"] == -32700
    assert resp["id"] is None


def test_invalid_request_shapes(tmp_path):
    db, _ = build_db(tmp_path)

    batch = rpc('[{"jsonrpc": "2.0", "id": 1, "method": "stats"}]', db)
    assert batch["error"]["code"] == -32600

    bad_version = rpc(json.dumps({"jsonrpc": "1.0", "id": 1, "method": "stats"}), db)
    assert bad_version["error"]["code"] == -32600

    positional = rpc(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "stats", "params": []}), db)
    assert positional["error"]["code"] == -32600


def test_notification_gets_no_response(tmp_path):
    db, con = build_db(tmp_path)
    insert(con)
    con.commit()

    # 无 id:合法 notification 与非法 notification 都不回包
    assert rpc(json.dumps({"jsonrpc": "2.0", "method": "stats"}), db) is None
    assert rpc(json.dumps({"method": "stats"}), db) is None
    assert rpc("", db) is None


def test_server_error_on_missing_db(tmp_path):
    resp = rpc(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "stats"}), tmp_path / "ghost.sqlite")
    assert resp["error"]["code"] == -32000


# -- rpc.discover ------------------------------------------------------------


def test_rpc_discover_descriptor(tmp_path):
    db, _ = build_db(tmp_path)
    resp = rpc(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "rpc.discover"}), db)
    desc = resp["result"]["data"]

    assert desc["openrpc"] == "1.3.2"
    assert desc["info"]["title"] == "zedhub"
    names = [m["name"] for m in desc["methods"]]
    assert names == sorted(["threads.list", "threads.show", "projects", "stats"])
    assert "rpc.discover" not in names  # 元方法自身不列(OpenRPC 惯例)

    show = next(m for m in desc["methods"] if m["name"] == "threads.show")
    assert show["summary"] == "Show one thread by uuid."
    assert show["params"][0]["name"] == "thread_id"
    assert show["params"][0]["required"] is True
    assert show["result"]["schema"]["type"] == "object"

    listing = next(m for m in desc["methods"] if m["name"] == "threads.list")
    archived = next(p for p in listing["params"] if p["name"] == "archived")
    assert archived["required"] is False
    assert archived["schema"]["enum"] == ["no", "only", "all"]
    assert archived["schema"]["default"] == "no"
    limit = next(p for p in listing["params"] if p["name"] == "limit")
    assert limit["schema"]["type"] == "integer"


def test_rpc_discover_works_without_db(tmp_path):
    """discover 是纯元数据:数据库不存在也必须成功(不触发 -32000)。"""
    resp = rpc(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "rpc.discover"}), tmp_path / "ghost.sqlite")
    assert resp["result"]["data"]["info"]["title"] == "zedhub"


def test_serve_loop_one_shot(tmp_path):
    """多条输入逐行应答,EOF 结束,退出码 0——模拟管道 one-shot 调用。"""
    db, con = build_db(tmp_path)
    tid = insert(con, title="loop")
    con.commit()

    lines = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "threads.list"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "threads.show", "params": {"thread_id": tid}}),
        "",  # 空行必须被忽略
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "bogus"}),
    ]) + "\n"

    result = runner.invoke(app, ["rpc", "--db", str(db)], input=lines)
    assert result.exit_code == 0, result.output

    out_lines = [json.loads(l) for l in result.output.strip().splitlines()]
    assert len(out_lines) == 3
    assert out_lines[0]["result"]["data"][0]["title"] == "loop"
    assert out_lines[1]["result"]["data"]["id"] == tid
    assert out_lines[2]["error"]["code"] == -32601
