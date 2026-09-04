"""配合 LoginRequiredMiddleware 的公开视图混入。

Django 的 as_view() 会把 dispatch 上被装饰器设置的属性拷贝到视图函数上，
中间件据此识别「无需登录」的类视图（含 DRF 的 APIView/ViewSet）。
"""

from typing import Any

from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse
from django.utils.decorators import method_decorator


class PublicViewMixin:
    @method_decorator(login_not_required)
    def dispatch(self, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(*args, **kwargs)  # type: ignore[misc]
