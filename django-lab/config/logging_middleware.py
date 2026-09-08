"""structlog 请求上下文中间件：每个请求自动绑定 method/path/user，日志自动携带。

设计思想「上下文随请求流动」：业务代码写日志时只管业务字段，
请求维度的公共字段由 contextvars 统一注入 —— contextvars 协程安全，
ASGI 下各请求独立，不会串数据。
"""

from collections.abc import Callable

import structlog
from django.http import HttpRequest, HttpResponse


class RequestLogContextMiddleware:
    """请求开始时绑定上下文，请求结束后解绑。

    放在认证中间件之后：进入 __call__ 时 request.user 已解析完成。
    finally 里解绑是给 WSGI 线程复用兜底，避免残留脏上下文。
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        structlog.contextvars.bind_contextvars(
            http_method=request.method,
            http_path=request.path,
            user=getattr(user, "pk", None),
        )
        try:
            return self.get_response(request)
        finally:
            structlog.contextvars.unbind_contextvars("http_method", "http_path", "user")
