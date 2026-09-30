"""daemon HTTP API：HTTP+JSON 唯一契约（设计 §5，v2 标准）。

路由从 contract.HTTP_ENDPOINTS 声明表生成（与 schema 导出同源）；JSON
包络沿用 v1 规则；同步 Service 调用经有界线程池；写流水线全局互斥；
Host 头回环校验 + 可选 buildID 握手。
"""

from __future__ import annotations

import asyncio
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import api
from .buildid import build_id, package_version
from .contract import HTTP_ENDPOINTS, HttpEndpoint, find_endpoint
from .core.errors import (
    EXIT_CODE,
    HTTP_STATUS,
    DaemonUnreachableError,
    ErrorCode,
    HandshakeMismatchError,
    InvalidParamsError,
    InvalidRequestError,
    MethodNotSupportedError,
    ZedhubError,
)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

# 线程池上限（同步 SQLite/快照调用的并发界；WS 共用同一界）
SERVICE_POOL_SIZE = 8


def _err_payload(exc: ZedhubError) -> dict:
    return {"ok": False, "error": exc.to_payload()}


def _ok_payload(data, *, elapsed_ms: int, count: int | None = None) -> dict:
    meta: dict = {"elapsed_ms": elapsed_ms}
    if count is not None:
        meta["count"] = count
    return {"ok": True, "data": data, "meta": meta}


def _host_allowed(request: Request) -> bool:
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip().lower()
    # [::1]:8766 形式去括号后比较
    return host in LOOPBACK_HOSTS or host.strip("[]") in LOOPBACK_HOSTS


def _check_handshake(request: Request) -> None:
    """可选握手：壳必带 X-Zedhub-Build；第三方无头放行。"""
    client_build = request.headers.get("x-zedhub-build")
    if client_build and client_build != build_id():
        raise HandshakeMismatchError(
            f"daemon 是旧构建 {build_id()}，请重启后重试（client={client_build}）"
        )


class DaemonState:
    """daemon 进程内共享状态：上下文、写互斥、在途计数（空闲退出用）。"""

    def __init__(self, *, zed_db: Path | None = None, opencode_db: Path | None = None) -> None:
        self.ctx = api.CallContext(zed_db=zed_db, opencode_db=opencode_db)
        self.write_lock = asyncio.Lock()
        self.inflight = 0
        self.last_activity = time.monotonic()
        self.started_at = datetime.now(timezone.utc)


async def _run_in_pool(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args))


def _extract_params(request: Request, endpoint: HttpEndpoint, body: dict) -> dict:
    """从 query/path/body 组装业务 params（业务校验仍在 api 层）。"""
    params: dict = dict(body)
    if endpoint.method == "GET":
        for key in request.query_params.keys():
            value = request.query_params[key]
            # query string 天然 str：按契约类型 coerce，与 body/RPC 口径一致
            from .contract import QUERY_PARAM_TYPES

            if QUERY_PARAM_TYPES.get(key) == "integer":
                try:
                    value = int(value)
                except ValueError:
                    raise InvalidParamsError(
                        f"param '{key}' must be an integer, got {value!r}"
                    ) from None
            params.setdefault(key, value)
        for pp in endpoint.path_params:
            if pp in request.path_params:
                params[pp] = request.path_params[pp]
    return params


async def dispatch(request: Request) -> Response:
    started = time.perf_counter()
    state: DaemonState = request.app.state.daemon
    try:
        if not _host_allowed(request):
            return JSONResponse(
                {"ok": False, "error": {"code": "forbidden", "message": "non-loopback Host header"}},
                status_code=403,
            )
        endpoint = find_endpoint(request.method, request.url.path)
        if endpoint is None:
            raise MethodNotSupportedError(
                f"endpoint not supported: {request.method} {request.url.path}"
            )
        _check_handshake(request)

        if endpoint.api_method == "":  # health：基础设施端点
            data = {
                "build_id": build_id(),
                "version": package_version(),
                "started_at": state.started_at.astimezone().isoformat(timespec="seconds"),
            }
            return JSONResponse(_ok_payload(data, elapsed_ms=_ms(started)))

        body: dict = {}
        if endpoint.body_model is not None:
            try:
                raw = await request.json()
            except Exception as exc:
                raise InvalidRequestError(f"request body is not valid JSON: {exc}") from exc
            if not isinstance(raw, dict):
                raise InvalidRequestError("request body must be a JSON object")
            try:
                body = endpoint.body_model(**raw).model_dump()
            except Exception as exc:
                raise InvalidParamsError(f"invalid request body: {exc}") from exc

        params = _extract_params(request, endpoint, body)
        state.inflight += 1
        try:
            data = await _run_in_pool(api.call, endpoint.api_method, params, state.ctx)
        finally:
            state.inflight -= 1
            state.last_activity = time.monotonic()  # 空闲计时重置（请求结束时刻）
        count = len(data) if isinstance(data, list) else None
        return JSONResponse(_ok_payload(data, elapsed_ms=_ms(started), count=count))
    except ZedhubError as exc:
        status = HTTP_STATUS.get(exc.code, 500)
        return JSONResponse(_err_payload(exc), status_code=status)
    except Exception:  # noqa: BLE001 — 协议边界，任何意外都不能带崩 daemon
        traceback.print_exc(file=sys.stderr)
        exc = ZedhubError("internal error (see daemon stderr)")
        exc.code = ErrorCode.INTERNAL_ERROR
        return JSONResponse(_err_payload(exc), status_code=500)


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def _not_found(request: Request, exc: Exception) -> Response:
    """未注册路径的统一 JSON 404（method_not_supported）。"""
    return JSONResponse(
        {
            "ok": False,
            "error": {
                "code": "method_not_supported",
                "message": f"endpoint not supported: {request.method} {request.url.path}",
            },
        },
        status_code=404,
    )


def build_app(
    *,
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
    extra_routes: list[Route] | None = None,
    lifespan=None,
) -> Starlette:
    """组装 daemon HTTP 应用；WS（任务 11）经 extra_routes 注入。"""
    routes = [
        Route(ep.path, dispatch, methods=[ep.method])
        for ep in HTTP_ENDPOINTS
    ]
    if extra_routes:
        routes.extend(extra_routes)
    kwargs: dict = {"routes": routes, "exception_handlers": {404: _not_found, 405: _not_found}}
    if lifespan is not None:
        kwargs["lifespan"] = lifespan
    app = Starlette(**kwargs)
    app.state.daemon = DaemonState(zed_db=zed_db, opencode_db=opencode_db)
    return app


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
    on_start=None,
    on_shutdown=None,
) -> None:
    """前台运行 daemon（uvicorn 承载）；lifecycle（任务 7）挂 on_* 钩子。"""
    import contextlib

    import uvicorn
    from concurrent.futures import ThreadPoolExecutor

    @contextlib.asynccontextmanager
    async def lifespan(app):
        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(
            max_workers=SERVICE_POOL_SIZE, thread_name_prefix="zedhub-svc"
        )
        loop.set_default_executor(pool)  # 有界线程池：同步 Service 调用的并发界
        if on_start is not None:
            r = on_start(server, app)  # 钩子拿到 server 引用（空闲退出触发用）
            if asyncio.iscoroutine(r):
                await r
        try:
            yield
        finally:
            if on_shutdown is not None:
                r = on_shutdown()
                if asyncio.iscoroutine(r):
                    await r
            pool.shutdown(wait=True)  # 等待线程池内任务收尾，不泄漏

    app = build_app(
        zed_db=zed_db, opencode_db=opencode_db, lifespan=lifespan
    )
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()
