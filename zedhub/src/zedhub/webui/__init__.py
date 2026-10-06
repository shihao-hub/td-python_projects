"""Web 检索页：daemon 托管的静态薄客户端（v2 §1 硬规则）。

- 页面只经 ``/api/v1`` 取数，**不含业务逻辑**：GUI 与 CLI/MCP 同为壳；
- 资源随包分发（原生 HTML/CSS/JS，零构建链、零新增进程）；
- 与 API 同一安全防线：``Host`` 头必须回环（防 DNS rebinding 类探测），
  响应一律 ``Cache-Control: no-store``（开发期改页面即时生效）；
- 只暴露白名单后缀的静态文件，页面上不列目录、不读任意路径。
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

WEBUI_DIR = Path(__file__).parent
UI_PATH = "/ui"

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
# 静态资源白名单后缀（含路径分隔符/隐藏文件的请求一律拒绝）
ALLOWED_SUFFIXES = {".html", ".js", ".css", ".svg", ".ico", ".png", ".json"}


def _host_allowed(request: Request) -> bool:
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip().lower()
    return host in LOOPBACK_HOSTS or host.strip("[]") in LOOPBACK_HOSTS


def _forbidden() -> Response:
    return JSONResponse(
        {"ok": False, "error": {"code": "forbidden", "message": "non-loopback Host header"}},
        status_code=403,
    )


def _file_response(path: Path) -> Response:
    # no-store：开发期改页面不必清缓存；页面本身很小，缓存收益无意义
    return FileResponse(path, headers={"Cache-Control": "no-store"})


async def root(request: Request) -> Response:
    """根路径跳转到检索页（保持地址栏可收藏、可分享）。"""
    if not _host_allowed(request):
        return _forbidden()
    return RedirectResponse(UI_PATH, status_code=302)


async def index(request: Request) -> Response:
    if not _host_allowed(request):
        return _forbidden()
    return _file_response(WEBUI_DIR / "index.html")


async def asset(request: Request) -> Response:
    if not _host_allowed(request):
        return _forbidden()
    name = request.path_params.get("asset", "")
    target = (WEBUI_DIR / name).resolve()
    if (
        not name
        or "/" in name
        or "\\" in name
        or name.startswith(".")
        or target.suffix not in ALLOWED_SUFFIXES
        or target.parent != WEBUI_DIR.resolve()
        or not target.is_file()
    ):
        return JSONResponse(
            {"ok": False, "error": {"code": "not_found", "message": f"no such asset: {name}"}},
            status_code=404,
        )
    return _file_response(target)


def static_routes() -> list[Route]:
    """daemon 侧挂载点（http_api.build_app 调用）。"""
    return [
        Route("/", root, methods=["GET"]),
        Route(UI_PATH, index, methods=["GET"]),
        Route(f"{UI_PATH}/{{asset}}", asset, methods=["GET"]),
    ]
