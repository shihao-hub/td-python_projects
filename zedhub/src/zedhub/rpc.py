"""兼容 JSON-RPC 2.0 薄壳（stdio，方法表冻结，FR-9）。

- 逐行读入 JSON-RPC 请求，业务方法经 HTTP 调 daemon；payload 与错误码
  维持归档基线（-32602/-32001/-32000 等）。
- ``rpc.discover`` 为纯元数据，本地静态响应，不依赖 daemon（AC-10/AC-18）。
- 新增业务能力不进入本方法表（冻结）；daemon 连接失败映射 -32000 并附
  「先运行 zedhub serve」提示。

错误码（JSON-RPC 2.0 spec + zedhub server codes，与归档基线一致）：
    -32700 parse error / -32600 invalid request / -32601 method not found
    -32602 invalid params / -32603 internal error
    -32000 server error（快照/schema/daemon 不可达）/ -32001 not found
"""

from __future__ import annotations

import json
import sys
import time
from importlib.metadata import PackageNotFoundError, version as pkg_version
from typing import Any

from .client import DaemonClient
from .contract import RPC_METHODS, RPC_METHOD_SPECS, RPC_PARAM_DOCS, RPC_PARAM_SPECS
from .core.errors import RPC_CODE, ZedhubError

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SERVER_ERROR = -32000
NOT_FOUND = -32001

OPENRPC_VERSION = "1.3.2"


def _zedhub_version() -> str:
    try:
        return pkg_version("zedhub")
    except PackageNotFoundError:
        return "0.0.0"


def discover() -> dict:
    """OpenRPC 风格服务描述（来自 contract 方法表，零数据库依赖）。"""
    methods = []
    for name in sorted(RPC_METHOD_SPECS):
        spec = RPC_METHOD_SPECS[name]
        methods.append(
            {
                "name": name,
                "summary": spec["summary"],
                "params": [
                    {
                        "name": p,
                        "description": RPC_PARAM_DOCS[p],
                        "required": p in spec["required"],
                        "schema": RPC_PARAM_SPECS[p],
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


# 元方法：纯元数据，不经 daemon
META_METHODS: dict[str, Any] = {
    "rpc.discover": lambda params: discover(),
}


def _error_response(id_: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "error": err, "id": id_}


def _ok_response(id_: Any, payload: Any, started: float) -> dict:
    # 与 CLI 信封同名的元数据字段（归档基线），消费方认知统一
    return {
        "jsonrpc": "2.0",
        "result": {
            "data": payload,
            "count": len(payload) if isinstance(payload, list) else 1,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        },
        "id": id_,
    }


def _http_for_method(method: str, params: dict, client: DaemonClient):
    """业务方法 → HTTP 调用（contract 方法表映射）。"""
    http_method, path_tpl = RPC_METHODS[method]
    path = path_tpl
    for key in ("thread_id",):
        if "{" + key + "}" in path:
            path = path.replace("{" + key + "}", str(params.get(key, "")))
    return client.call(http_method, path, query=params if http_method == "GET" else None)


def handle_request(req: Any, client: DaemonClient) -> dict | None:
    """处理一个已解析的请求；返回响应 dict，notification 返回 None。"""
    if not isinstance(req, dict):
        return _error_response(None, INVALID_REQUEST, "request must be a JSON object (batch arrays are not supported)")

    id_ = req.get("id")  # 保留原样回填；缺失即 notification
    has_id = "id" in req

    def fail(code: int, message: str, data: Any = None) -> dict | None:
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
            payload = meta_fn(params or {})
        elif req["method"] in RPC_METHODS:
            payload = _http_for_method(req["method"], params or {}, client)
        else:
            return fail(METHOD_NOT_FOUND, f"method not found: {req['method']} (available: {', '.join(sorted(RPC_METHODS))})")
    except ZedhubError as exc:
        code = RPC_CODE.get(exc.code, SERVER_ERROR)
        return fail(code, str(exc))
    except Exception as exc:  # noqa: BLE001 — 协议边界，任何意外都不能崩掉循环
        return fail(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

    return _ok_response(id_, payload, started) if has_id else None


def handle_line(line: str, client: DaemonClient) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        return _error_response(None, PARSE_ERROR, f"parse error: {exc}")
    return handle_request(req, client)


def serve() -> None:
    """行分隔 JSON-RPC 2.0 stdio 循环（EOF 结束）。"""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")  # Windows 默认 GBK，必须切 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    client = DaemonClient()
    for line in sys.stdin:
        resp = handle_line(line, client)
        if resp is not None:
            json.dump(resp, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            sys.stdout.flush()  # 一次性管道调用方需要立刻读到响应
