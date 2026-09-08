"""JSON-RPC 2.0 over line-delimited stdio (LSP-style framing).

One JSON object per line in, one JSON object per line out. A single piped
line behaves as a one-shot call (EOF ends the loop); a long-lived client
may keep the pipe open and send more lines. Each request opens a fresh
snapshot of the Zed database, so responses always reflect current data.

Error codes (JSON-RPC 2.0 spec + zedhub server codes):
    -32700 parse error          line is not valid JSON
    -32600 invalid request      not a request object / batch not supported
    -32601 method not found
    -32602 invalid params
    -32603 internal error
    -32000 server error         snapshot/schema failure
    -32001 not found            e.g. unknown thread id

Notifications (requests without "id") are executed but never answered,
per spec. Batch (array) requests are not supported and answered with
-32600.

Service discovery: the reserved method ``rpc.discover`` returns an
OpenRPC-style descriptor (method list, param schemas, descriptions)
built from api.METHOD_SPECS — the same single source of truth the
dispatcher uses, so it can never drift. It is pure metadata and runs
without touching the database. https://spec.openrpc.org
"""

from __future__ import annotations

import json
import sys
import time
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from typing import Any

from .api import METHOD_SPECS, PARAM_DOCS, PARAM_SPECS, ApiError, SERVER_ERROR, call
from .core.repo import SchemaError, ZedDb
from .core.service import Service
from .core.snapshot import SnapshotError, open_snapshot

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
INTERNAL_ERROR = -32603

OPENRPC_VERSION = "1.3.2"


def _zedhub_version() -> str:
    try:
        return pkg_version("zedhub")
    except PackageNotFoundError:
        return "0.0.0"


def discover() -> dict:
    """Build the OpenRPC-style service descriptor from METHOD_SPECS."""
    methods = []
    for name in sorted(METHOD_SPECS):
        spec = METHOD_SPECS[name]
        methods.append(
            {
                "name": name,
                "summary": spec["summary"],
                "params": [
                    {
                        "name": p,
                        "description": PARAM_DOCS[p],
                        "required": p in spec["required"],
                        "schema": PARAM_SPECS[p],
                    }
                    for p in spec["params"]
                ],
                "result": {"name": "data", "schema": {"type": spec["result_type"]}},
            }
        )
    return {
        "openrpc": OPENRPC_VERSION,
        "info": {
            "title": "zedhub",
            "version": _zedhub_version(),
            "description": "Read-only queries over Zed's agent session database.",
        },
        "methods": methods,
    }


# 元方法:纯元数据,不经过快照路径(数据库缺失时也可用)
META_METHODS: dict[str, Any] = {
    "rpc.discover": lambda params: discover(),
}


def _error_response(id_: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "error": err, "id": id_}


def _ok_response(id_: Any, payload: Any, started: float) -> dict:
    # 与 CLI 信封同名的元数据字段,消费方认知统一
    return {
        "jsonrpc": "2.0",
        "result": {
            "data": payload,
            "count": len(payload) if isinstance(payload, list) else 1,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        },
        "id": id_,
    }


def handle_request(req: Any, db: Path | None) -> dict | None:
    """Handle one already-JSON-parsed payload; return the response dict or
    None for notifications (which must not be answered, per spec)."""
    if not isinstance(req, dict):
        return _error_response(None, INVALID_REQUEST, "request must be a JSON object (batch arrays are not supported)")

    id_ = req.get("id")  # 保留原样回填;缺失即 notification
    has_id = "id" in req

    def fail(code: int, message: str, data: Any = None) -> dict | None:
        # 无 id 的非法请求无法应答,规范允许静默丢弃
        return _error_response(id_, code, message, data) if has_id else None

    if req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
        return fail(INVALID_REQUEST, 'expected {"jsonrpc": "2.0", "method": <string>, ...}')
    params = req.get("params")
    if params is not None and not isinstance(params, dict):
        return fail(INVALID_REQUEST, "params must be an object (positional arrays are not supported)")

    started = time.perf_counter()
    try:
        meta_fn = META_METHODS.get(req["method"])
        if meta_fn is not None:
            # rpc.discover 等元方法:纯元数据,不需要数据库快照
            payload = meta_fn(params or {})
        else:
            # 每请求独立快照:响应永远基于当前数据
            with open_snapshot(db) as snap:
                with ZedDb(snap) as conn:
                    payload = call(req["method"], params or {}, Service(conn))
    except ApiError as exc:
        return fail(exc.code, exc.message, exc.data)
    except SnapshotError as exc:
        return fail(SERVER_ERROR, str(exc), {"type": type(exc).__name__})
    except SchemaError as exc:
        return fail(SERVER_ERROR, str(exc), {"type": type(exc).__name__})
    except Exception as exc:  # noqa: BLE001 — 协议边界,任何意外都不能崩掉循环
        return fail(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

    return _ok_response(id_, payload, started) if has_id else None


def handle_line(line: str, db: Path | None) -> dict | None:
    """Parse one input line and produce its response (None = no answer)."""
    line = line.strip()
    if not line:
        return None
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        return _error_response(None, PARSE_ERROR, f"parse error: {exc}")
    return handle_request(req, db)


def serve(db: Path | None = None) -> None:
    """Line-delimited JSON-RPC 2.0 loop over stdin/stdout until EOF."""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")  # Windows 默认 GBK,必须切 UTF-8
    for line in sys.stdin:
        resp = handle_line(line, db)
        if resp is not None:
            json.dump(resp, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            sys.stdout.flush()  # one-shot 管道调用方需要立刻读到响应
