"""DRF ViewSet：一个类声明全部 REST 动作，Router 负责生成 URL。

与 FastAPI 装饰器逐个注册路由不同，这里是「视图类 + 路由器」的组合式设计；
权限、认证、限流、分页都在 settings.REST_FRAMEWORK 全局声明。
"""

from django.db.models import Count, Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from catalog.forms import BorrowForm
from catalog.models import Author, Book, BorrowRecord, Status

from .serializers import AuthorSerializer, BookSerializer, BorrowRecordSerializer

# 注意：DRF 3.18 起 ViewSet 会主动豁免 Django 的 LoginRequiredMiddleware，
# 访问控制交回 DRF 权限体系（settings.REST_FRAMEWORK + 各视图的 permission_classes）。


class BookViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Book.objects.select_related("author").prefetch_related("genres")
    serializer_class = BookSerializer

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def borrow(self, request, pk=None):
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
