"""FastAPI 应用装配：lifespan 管理引擎，异常统一翻译成 CommonResult 信封。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from apps.fastapi_layered_api.router import CommonResult, router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    from apps.fastapi_layered_api.models import engine

    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="tlr layered api", version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    @app.exception_handler(HTTPException)
    async def http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """业务异常 -> 统一信封：HTTP 状态码保留，body 是 CommonResult 形状。"""
        return JSONResponse(
            status_code=exc.status_code,
            content=CommonResult[None].error(exc.status_code, str(exc.detail)).model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=CommonResult[None].error(500, f"internal error: {exc}").model_dump(),
        )

    return app


app = create_app()
