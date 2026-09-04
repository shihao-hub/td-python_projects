"""Django 测试哲学：自带 test client（不启 HTTP 服务）、内存事务回滚、
assertContains/assertRedirects 等「Web 级」断言开箱即用。
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from .forms import BorrowForm
from .models import Author, Book, BookGenre, BorrowRecord, Genre, Status

User = get_user_model()


def make_book(title="测试书", price="50.00", status=Status.AVAILABLE, **kwargs):
    author, _ = Author.objects.get_or_create(first_name="测", last_name="试")
    kwargs.setdefault("slug", f"book-{Book.objects.count()}-{abs(hash(title)) % 10000}")
    return Book.objects.create(
        title=title, author=author, price=Decimal(price), status=status, **kwargs
    )


class ModelTests(TestCase):
    def test_text_choices(self):
        # 枚举值 / 中文 label 一处定义，admin、表单、API 全部复用
        self.assertEqual(Status.BORROWED, "BORROWED")
        self.assertEqual(Status.BORROWED.label, "已借出")
        self.assertEqual(next(iter(Status.choices)), ("DRAFT", "草稿"))

    def test_db_default_and_generated_field(self):
        book = make_book(price="100.00")
        book.refresh_from_db()
        # db_default：created_at 由数据库在 INSERT 时填充
        self.assertIsNotNone(book.created_at)
        # GeneratedField：含税价由数据库生成列计算
        self.assertEqual(book.price_with_tax, Decimal("109.00"))

    def test_book_str_and_absolute_url(self):
        book = make_book()
        self.assertEqual(str(book), "测试书")
        self.assertEqual(book.get_absolute_url(), f"/books/{book.slug}/")

    def test_custom_queryset_manager(self):
        make_book("在架书")
        borrowed = make_book("在借书")
        borrowed.status = Status.BORROWED
        borrowed.save()
        titles = list(Book.objects.available().values_list("title", flat=True))
        self.assertIn("在架书", titles)
        self.assertNotIn("在借书", titles)

    def test_borrow_flow_updates_status_via_signal(self):
        user = User.objects.create_user("reader", password="x")
        book = make_book()
        record = BorrowRecord.objects.create(
            book=book, borrower=user, due_date=date.today() + timedelta(days=7)
        )
        book.refresh_from_db()
        self.assertEqual(book.status, Status.BORROWED)
        self.assertTrue(record.is_active)
        record.mark_returned()
        book.refresh_from_db()
        self.assertEqual(book.status, Status.AVAILABLE)
        self.assertFalse(record.is_active)


class ConcurrencyTests(TransactionTestCase):
    """部分唯一约束要真实提交才能触发，用 TransactionTestCase。"""

    def test_unique_active_borrow_per_book(self):
        user = User.objects.create_user("reader", password="x")
        book = make_book()
        BorrowRecord.objects.create(book=book, borrower=user, due_date=date(2026, 12, 1))
        with self.assertRaises(IntegrityError):
            BorrowRecord.objects.create(book=book, borrower=user, due_date=date(2026, 12, 2))


class FormTests(TestCase):
    def test_borrow_form_rejects_unavailable_book(self):
        user = User.objects.create_user("reader", password="x")
        book = make_book(status=Status.RETIRED)
        form = BorrowForm({"due_date": date.today() + timedelta(days=3)}, book=book, borrower=user)
        self.assertFalse(form.is_valid())
        self.assertIn("不可借阅", str(form.errors))

    def test_borrow_form_rejects_past_due_date(self):
        user = User.objects.create_user("reader", password="x")
        book = make_book()
        form = BorrowForm({"due_date": date.today() - timedelta(days=1)}, book=book, borrower=user)
        self.assertFalse(form.is_valid())

    def test_borrow_form_initial_is_callable(self):
        form = BorrowForm()
        # 字段 initial 支持 callable（惰性求值），BoundField 渲染时才调用
        self.assertEqual(form.fields["due_date"].initial(), date.today() + timedelta(days=14))


class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("reader", password="reader1234")
        cls.book = make_book("视图测试书")
        genre, _ = Genre.objects.get_or_create(name="编程")
        BookGenre.objects.create(book=cls.book, genre=genre)

    def test_public_pages_exempt_from_login_middleware(self):
        # LoginRequiredMiddleware 全局生效，公开页靠 @login_not_required 豁免
        for url_name, kwargs in [
            ("catalog:books", None),
            ("catalog:book-detail", {"slug": self.book.slug}),
            ("catalog:authors", None),
            ("catalog:dashboard", None),
        ]:
            url = reverse(url_name, kwargs=kwargs) if kwargs else reverse(url_name)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_my_loans_requires_login(self):
        response = self.client.get(reverse("catalog:my-loans"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_borrow_and_return_flow(self):
        self.client.force_login(self.user)
        url = reverse("catalog:book-borrow", kwargs={"slug": self.book.slug})
        due = date.today() + timedelta(days=7)
        response = self.client.post(url, {"due_date": due.isoformat()}, follow=True)
        self.assertContains(response, "借阅成功")
        self.book.refresh_from_db()
        self.assertEqual(self.book.status, Status.BORROWED)
        record = self.user.borrow_records.get()
        response = self.client.post(
            reverse("catalog:loan-return", kwargs={"pk": record.pk}), follow=True
        )
        self.assertContains(response, "已归还")
        self.book.refresh_from_db()
        self.assertEqual(self.book.status, Status.AVAILABLE)

    def test_borrow_rejects_get(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("catalog:book-borrow", kwargs={"slug": self.book.slug}))
        self.assertEqual(response.status_code, 405)


class TaskTests(TestCase):
    def test_notify_book_returned_task_runs(self):
        from django.tasks import TaskResultStatus

        from .tasks import notify_book_returned

        result = notify_book_returned.enqueue("某本书", "某人")
        # immediate 后端：enqueue 后即执行完毕
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
