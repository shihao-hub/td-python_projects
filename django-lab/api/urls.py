from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

app_name = "api"

router = DefaultRouter()
router.register("books", views.BookViewSet, basename="book")
router.register("authors", views.AuthorViewSet, basename="author")
router.register("loans", views.BorrowRecordViewSet, basename="loan")

urlpatterns = [
    *router.urls,
    # Session 认证端点（@login_not_required 显式豁免 LoginRequiredMiddleware）
    path("auth/login/", views.login_view, name="auth-login"),
    path("auth/logout/", views.logout_view, name="auth-logout"),
    path("auth/me/", views.me_view, name="auth-me"),
]
