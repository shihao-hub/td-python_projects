"""WebSocket 学习通道（一次性实现，冻结；设计 §10 / NFR-7）。

- 运行于 daemon 进程内（与 uvicorn 共享事件循环与线程池），默认
  127.0.0.1:8765；白名单仅 ``threads.list`` 与 ``stats``，参数校验直调
  ``api.call()``（与 HTTP 同源，AC-9/AC-15）。
- 角色边界：这是对外只读查询通道，不是 Agent 运行时桥接协议——不启动
  Agent 引擎、不处理 ACP/protobuf/内部桥接消息（AC-17）；写操作不可达
  （AC-16）。
- 冻结声明：本轮实现后不再新增方法或能力；生产与自动化用途一律使用
  HTTP API（README 与 schema 的 ws 段同此声明）。

资源纪律：每连接请求串行、单请求 30 秒上限、断连取消等待中的任务；
同步 SQLite 调用依靠数据库超时与 ``finally`` 清理，不承诺瞬时中断。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from urllib.parse import urlsplit

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from . import api
from .contract import WS_FREEZE_NOTE, WS_METHODS, WS_PROTOCOL
from .core.errors import (
    MethodNotSupportedError,
    InvalidRequestError,
    ZedhubError,
)

WS_HOST = "127.0.0.1"
WS_PORT = 8765
MAX_CONNECTIONS = 8
MAX_FRAME_BYTES = WS_PROTOCOL["max_frame_bytes"]
REQUEST_TIMEOUT_S = 30
OVERLOAD_CLOSE_CODE = 1013

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _host_of(value: str) -> str:
    return (urlsplit("//" + value).hostname or value).strip("[]").lower()


def _origin_or_host_ok(headers) -> bool:
    """Origin（带则校验）与 Host 必须回环；挡浏览器跨站探测。"""
    origin = headers.get("Origin")
    if origin is not None and _host_of(urlsplit(origin).netloc or origin) not in _LOOPBACK:
        return False
    host = headers.get("Host")
    if host is not None and _host_of(host) not in _LOOPBACK:
        return False
    return True


class WsChannel:
    """daemon 内的 WS 监听器：连接计数 + 请求分发。"""

    def __init__(self, state) -> None:
        self.state = state  # http_api.DaemonState（ctx 与线程池共享）
        self.connections = 0

    async def _call_api(self, method: str, params: dict):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, api.call, method, params, self.state.ctx)

    async def handle_message(self, raw: str) -> dict:
        """单条 JSON 文本帧 → 响应信封（含 30 秒上限）。"""
        started = time.perf_counter()
        try:
            try:
                req = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise InvalidRequestError(f"invalid JSON: {exc}") from exc
            if not isinstance(req, dict):
                raise InvalidRequestError("request must be a JSON object")
            method = req.get("method")
            if not isinstance(method, str) or not method:
                raise InvalidRequestError("field 'method' (string) is required")
            rid = req.get("id") if isinstance(req.get("id"), (int, float, str)) else None
            if method not in WS_METHODS:
                raise MethodNotSupportedError(
                    f"method not supported: {method} (supported: {', '.join(WS_METHODS)}; "
                    f"note: {WS_FREEZE_NOTE})"
                )
            params = req.get("params") or {}
            if not isinstance(params, dict):
                raise InvalidRequestError("field 'params' must be an object")
            data = await asyncio.wait_for(
                self._call_api(method, params), timeout=REQUEST_TIMEOUT_S
            )
            return {
                "id": rid,
                "ok": True,
                "data": data,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
            }
        except asyncio.TimeoutError:
            return {
                "id": None,
                "ok": False,
                "error": {"code": "internal_error", "message": f"request exceeded {REQUEST_TIMEOUT_S}s"},
            }
        except ZedhubError as exc:
            return {"id": None, "ok": False, "error": exc.to_payload()}
        except Exception:  # noqa: BLE001 — 协议边界，任何意外都不能带崩通道
            traceback.print_exc(file=sys.stderr)
            return {
                "id": None,
                "ok": False,
                "error": {"code": "internal_error", "message": "internal error (see daemon stderr)"},
            }

    async def _serve_connection(self, conn: ServerConnection) -> None:
        if self.connections >= MAX_CONNECTIONS:
            await conn.close(OVERLOAD_CLOSE_CODE, "too many connections")
            return
        self.connections += 1
        try:
            # 每连接内请求串行：一条消息一个响应（text 帧已由库解码为 str）
            async for raw in conn:
                if isinstance(raw, (bytes, bytearray)):
                    await conn.close(1003, "binary frames not supported")
                    return
                resp = await self.handle_message(raw)
                await conn.send(json.dumps(resp, ensure_ascii=False))
        except ConnectionClosed:
            pass  # 客户端断开：取消等待中的任务由 asyncio 传播，资源在 finally 释放
        finally:
            self.connections -= 1

    async def start(self, *, host: str = WS_HOST, port: int = WS_PORT):
        """启动 WS 监听（daemon lifespan 内调用）。"""

        def process_request(conn: ServerConnection, request: Request) -> Response | None:
            if not _origin_or_host_ok(request.headers):
                return Response(403, "Forbidden", headers={"Connection": "close"})
            return None

        return await serve(
            self._serve_connection,
            host,
            port,
            max_size=MAX_FRAME_BYTES,
            process_request=process_request,
        )
