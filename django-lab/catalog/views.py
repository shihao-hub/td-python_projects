"""视图层（MTV 的 View）。

三种风格并存，各自对应一种 Django 思想：
- 泛型类视图（ListView/DetailView）：「少写代码」，声明式配置
- 函数视图：短小直给，适合单页逻辑
- async 视图 + async ORM（acount 等）：ASGI 原生协程支持
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required, login_required
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import FETCH_PEERS, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from .forms import BorrowForm
from .mixins import PublicViewMixin
from .models import Author, Book, BorrowRecord
from .tasks import notify_book_returned


class BookListView(PublicViewMixin, ListView):
    model = Book
    context_object_name = "books"
    paginate_by = 9
    template_name = "catalog/book_list.html"

    def get_queryset(self):
        return Book.objects.with_relations()


class BookDetailView(PublicViewMixin, DetailView):
    model = Book
    context_object_name = "book"
    template_name = "catalog/book_detail.html"

    def get_queryset(self):
        return Book.objects.select_related("author").prefetch_related("genres", "borrows")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["borrow_form"] = BorrowForm()
        return context


class AuthorListView(PublicViewMixin, ListView):
    context_object_name = "authors"
    template_name = "catalog/author_list.html"

    def get_queryset(self):
        # annotate：聚合下推到 SQL，避免模板里逐行 count（N+1）
        return Author.objects.annotate(book_count=Count("books"))


class AuthorDetailView(PublicViewMixin, DetailView):
    model = Author
    context_object_name = "author"
    template_name = "catalog/author_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Django 6.1+ fetch_mode(FETCH_PEERS)：只取列表页要用的字段，
        # 模板一旦碰到未取字段，为同一批实例一次性补齐（两条 SQL 解决 N+1）
        context["books"] = (
            self.object.books.only("title", "slug", "status", "price_with_tax", "author_id")
            .fetch_mode(FETCH_PEERS)
            .order_by("-created_at")
        )
        return context


@login_not_required
async def dashboard(request):
    """async 视图 + async ORM：await 查询不阻塞事件循环（Django 4.1+ 提供 a 系列方法）。"""
    total_books = await Book.objects.acount()
    available_books = await Book.objects.available().acount()
    active_loans = await BorrowRecord.objects.filter(returned_at__isnull=True).acount()
    total_authors = await Author.objects.acount()
    latest_books = [
        book async for book in Book.objects.with_relations().order_by("-created_at")[:5]
    ]
    return render(
        request,
        "catalog/dashboard.html",
        {
            "total_books": total_books,
            "available_books": available_books,
            "active_loans": active_loans,
            "total_authors": total_authors,
            "latest_books": latest_books,
        },
    )


@login_not_required
def book_search(request):
    query = (request.GET.get("q") or "").strip()
    books_qs = Book.objects.search(query) if query else Book.objects.with_relations()
    paginator = Paginator(books_qs, 9)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "catalog/book_list.html",
        {"books": page.object_list, "page_obj": page, "query": query},
    )


@login_required
@require_POST
def borrow_book(request, slug: str):
    book = get_object_or_404(Book, slug=slug)
    form = BorrowForm(request.POST, book=book, borrower=request.user)
    if form.is_valid():
        try:
            record = form.save()
        except IntegrityError:
            # 数据库部分唯一约束兜住并发：两个人同时点「借阅」只成功一个
            messages.error(request, "手慢了，这本书刚被别人借走。")
        else:
            messages.success(request, f"《{book.title}》借阅成功，请在 {record.due_date} 前归还。")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, str(error))
    # 传模型实例即可：redirect 会调用 get_absolute_url() —— DRY 的 URL 定义
    return redirect(book)


@login_required
def my_loans(request):
    loans = (
        BorrowRecord.objects.filter(borrower=request.user)
        .select_related("book", "book__author")
        .order_by("-borrowed_at")
    )
    return render(request, "catalog/my_loans.html", {"loans": loans})


@login_required
@require_POST
def return_book(request, pk: int):
    record = get_object_or_404(BorrowRecord, pk=pk, borrower=request.user)
    record.mark_returned()
    # Django 6.0+ django.tasks：后台任务解耦慢操作（immediate 后端同步执行，日志可见）
    notify_book_returned.enqueue(record.book.title, str(record.borrower))
    messages.success(request, f"《{record.book.title}》已归还，感谢！")
    return redirect("catalog:my-loans")
