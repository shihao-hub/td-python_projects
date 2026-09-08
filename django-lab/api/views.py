"""DRF ViewSet：一个类声明全部 REST 动作，Router 负责生成 URL。

与 FastAPI 装饰器逐个注册路由不同，这里是「视图类 + 路由器」的组合式设计；
权限、认证、限流、分页都在 settings.REST_FRAMEWORK 全局声明。
"""

from typing import override

import structlog
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_not_required
from django.db.models import Count, Q
from django.http import Http404
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.filters import SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from catalog.forms import BorrowForm
from catalog.models import Author, Book, BorrowRecord, Status
from catalog.tasks import notify_book_returned
from sqla_lab.queries import book_by_slug
from sqla_lab.session import session_scope

from .serializers import AuthorSerializer, BookSerializer, BorrowRecordSerializer

logger = structlog.get_logger(__name__)

# 注意：DRF 3.18 起 ViewSet 会主动豁免 Django 的 LoginRequiredMiddleware，
# 访问控制交回 DRF 权限体系（settings.REST_FRAMEWORK + 各视图的 permission_classes）。


class BookViewSet(viewsets.ReadOnlyModelViewSet):
    # slug 路由对齐页面端 /books/<slug>/，前端可以直接用 slug 定位详情
    lookup_field = "slug"
    serializer_class = BookSerializer
    # ?search= 全文搜索：字段集与页面端 BookQuerySet.search 保持一致
    filter_backends = [SearchFilter]
    search_fields = ["title", "summary", "author__first_name", "author__last_name"]

    def get_queryset(self):
        qs = Book.objects.with_relations()
        # ?author=<pk>：作者详情页取该作者的书（两行手写，不为此引入 django-filter）
        author = self.request.query_params.get("author")
        if author:
            qs = qs.filter(author_id=author)
        return qs

    @override
    def retrieve(self, request, *args, **kwargs):
        # detail 接口走 sqlalchemy 查询（对比 ORM 的演示路径），留一条日志便于观察
        logger.info("retrieve_via_sqlalchemy", slug=kwargs[self.lookup_field])
        slug = kwargs[self.lookup_field]  # lookup_field = "slug"
        with session_scope() as session:  # sqla_lab.session：事务+自动关闭
            book = book_by_slug(session, slug)
            if book is None:
                raise Http404  # DRF 转成 404 响应，行为对齐 get_object()
            return Response(self.get_serializer(book).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def borrow(self, request, slug=None):
        """借书动作：直接复用页面端的 BorrowForm 校验 —— 一处定义，两端使用。"""
        book = self.get_object()
        form = BorrowForm(request.data, book=book, borrower=request.user)
        if not form.is_valid():
            return Response({"detail": form.errors}, status=400)
        record = form.save()
        serializer = BorrowRecordSerializer(record, context={"request": request})
        return Response(serializer.data, status=201)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """聚合统计下推到数据库：一条 SQL 拿全部状态计数。"""
        data = self.get_queryset().aggregate(
            total=Count("id"),
            available=Count("id", filter=Q(status=Status.AVAILABLE)),
            borrowed=Count("id", filter=Q(status=Status.BORROWED)),
            draft=Count("id", filter=Q(status=Status.DRAFT)),
            retired=Count("id", filter=Q(status=Status.RETIRED)),
        )
        return Response(data)


class AuthorViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuthorSerializer

    def get_queryset(self):
        return Author.objects.annotate(book_count=Count("books"))


class BorrowRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """我的借阅记录：DRF 权限层先拒绝匿名请求（403），get_queryset 才会执行。"""

    serializer_class = BorrowRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            BorrowRecord.objects.filter(borrower=self.request.user)
            .select_related("book", "book__author")
            .order_by("-borrowed_at")
        )

    @action(detail=True, methods=["post"], url_path="return", url_name="return")
    def give_back(self, request, pk=None):
        """还书：POST /api/loans/{pk}/return/（url_path 避开 Python 关键字）。

        与页面端 return_book 视图同一套编排：模型方法改状态（信号自动同步图书状态），
        后台任务解耦通知 —— 归宿清晰的逻辑，两端零重复。
        """
        record = self.get_object()  # queryset 已限定本人，他人记录直接 404
        record.mark_returned()
        notify_book_returned.enqueue(record.book.title, str(record.borrower))
        serializer = self.get_serializer(record)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Session 认证端点：SPA 与后端同源（开发期 Vite 代理 /api），继续复用框架的
# SessionAuthentication + CSRF。me 挂 ensure_csrf_cookie，前端启动时调用一次
# 即可拿到 csrftoken cookie，后续 POST 统一带 X-CSRFToken 头。
# 注意：全局默认权限是 IsAuthenticatedOrReadOnly（POST 视为写操作），
# 这三个端点必须显式 AllowAny，否则匿名登录请求会被自己拦在门外。
# ---------------------------------------------------------------------------


@login_not_required
@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request: Request) -> Response:
    user = authenticate(
        request,
        username=request.data.get("username", ""),
        password=request.data.get("password", ""),
    )
    if user is None:
        return Response({"detail": "用户名或密码错误。"}, status=400)
    login(request, user)
    return Response(
        {"id": user.pk, "username": user.username, "is_staff": getattr(user, "is_staff", False)}
    )


@login_not_required
@api_view(["POST"])
@permission_classes([AllowAny])
def logout_view(request: Request) -> Response:
    logout(request)
    return Response(status=204)


@login_not_required
@api_view(["GET"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def me_view(request: Request) -> Response:
    user = request.user
    if not user.is_authenticated:
        return Response({"authenticated": False})
    return Response(
        {
            "authenticated": True,
            "id": user.pk,
            "username": getattr(user, "username", ""),
            "is_staff": getattr(user, "is_staff", False),
        }
    )
