"""MCP front: tool registration, schema/description, dispatch, stdio smoke."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from .fixture import insert, make_db

TOOL_NAMES = {"threads_list", "threads_show", "projects", "stats"}


def build_db(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    db_path = make_db(tmp_path)
    con = sqlite3.connect(db_path)
    return db_path, con


def payload(result) -> Any:
    """2.x 对 dict 返回值 structured_content 可用;list 返回被包成
    {"result": [...]};text 内容不可靠,仅作兜底。"""
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and set(sc) == {"result"}:
        return sc["result"]
    if sc is not None:
        return sc
    texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
    assert texts, f"no text content in result: {result!r}"
    return json.loads(texts[0])


def test_tools_registered_with_descriptions(tmp_path):
    from zedhub.mcp_server import build_server

    server = build_server(tmp_path / "unused.sqlite")
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == TOOL_NAMES

    threads_list = next(t for t in tools if t.name == "threads_list")
    # 描述来自 PARAM_DOCS(单一事实源),非空且包含参数说明
    assert "newest first" in threads_list.description
    assert "archived" in threads_list.description
    # schema 由签名生成;Optional 参数是 anyOf(nullable)
    props = threads_list.input_schema["properties"]
    assert set(props) == {"project", "agent", "archived", "since", "until", "search", "limit"}
    assert "integer" in json.dumps(props["limit"])
    assert "archived" not in threads_list.input_schema.get("required", [])


def test_call_tool_stats_and_threads(tmp_path):
    from zedhub.mcp_server import build_server

    db, con = build_db(tmp_path)
    tid = insert(con, title="mcp check")
    insert(con, title="gone", archived=1)
    con.commit()

    server = build_server(db)

    stats = asyncio.run(server.call_tool("stats", {}))
    assert payload(stats)["total_threads"] == 2

    listed = asyncio.run(server.call_tool("threads_list", {}))
    assert payload(listed)[0]["id"] == tid  # archived 默认隐藏

    shown = asyncio.run(server.call_tool("threads_show", {"thread_id": tid}))
    assert payload(shown)["title"] == "mcp check"


def test_call_tool_errors(tmp_path):
    from mcp.server.mcpserver.exceptions import ToolError

    from zedhub.mcp_server import build_server

    db, con = build_db(tmp_path)
    insert(con)
    con.commit()
    server = build_server(db)

    # 校验逻辑在 api.py;MCP 层转为 ToolError 且 message 保留原因
    with pytest.raises(ToolError) as ei:
        asyncio.run(server.call_tool("threads_list", {"archived": "maybe"}))
    assert "maybe" in str(ei.value)

    with pytest.raises(ToolError) as ei:
        asyncio.run(server.call_tool("threads_show", {"thread_id": "00000000-0000-0000-0000-000000000000"}))
    assert "not found" in str(ei.value)


def test_stdio_end_to_end(tmp_path):
    """真实子进程 + 官方客户端:initialize → tools/list → tools/call。"""
    db, con = build_db(tmp_path)
    insert(con, title="e2e")
    con.commit()

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    zedhub_exe = Path(sys.executable).parent / "zedhub.exe"
    assert zedhub_exe.exists(), f"console script not found: {zedhub_exe}"

    async def run() -> dict:
        params = StdioServerParameters(command=str(zedhub_exe), args=["mcp", "--db", str(db)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                stats = await session.call_tool("stats", {})
                listed = await session.call_tool("threads_list", {"search": "e2e"})
                return {
                    "tool_names": sorted(t.name for t in tools.tools),
                    "stats": payload(stats),
                    "listed": payload(listed),
                }

    out = asyncio.run(run())
    assert out["tool_names"] == sorted(TOOL_NAMES)
    assert out["stats"]["total_threads"] == 1
    assert out["listed"][0]["title"] == "e2e"
