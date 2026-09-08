"""根 URLconf：MTV 中的「路由层」——显式注册，一目了然。"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # batteries included：登录/登出/密码重置视图直接 include，零代码
    path("accounts/", include("django.contrib.auth.urls")),
    path("api/", include("api.urls")),
    path("compare/", include("sqla_lab.urls")),
    path("", include("catalog.urls")),
]

if __debug__:
    import debug_toolbar  # noqa: F401

    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
