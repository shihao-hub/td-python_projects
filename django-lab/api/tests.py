"""DRF 接口测试：APIClient + 反查 URL，验证 ViewSet/Router/权限组合。"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from catalog.models import Author, Book, Status

User = get_user_model()


class BookApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client = APIClient()
        cls.user = User.objects.create_user("reader", password="reader1234")
        cls.author = Author.objects.create(first_name="Simon", last_name="Willison")
        cls.book = Book.objects.create(
            title="Django 之道",
            slug="the-way-of-django",
            author=cls.author,
            price="59.00",
            status=Status.AVAILABLE,
        )
        cls.other_book = Book.objects.create(
            title="流畅的 Python",
            slug="fluent-python",
            author=Author.objects.create(first_name="Luciano", last_name="Ramalho"),
            price="139.00",
            status=Status.AVAILABLE,
        )

    def test_anonymous_can_read_list_and_detail(self):
        response = self.client.get(reverse("api:book-list"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.data)
        detail = self.client.get(reverse("api:book-detail", kwargs={"slug": self.book.slug}))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["title"], "Django 之道")
        self.assertEqual(detail.data["status_display"], "可借")
        self.assertTrue(detail.data["page_url"].endswith(f"/books/{self.book.slug}/"))

    def test_search_filter_matches_title_and_author(self):
        response = self.client.get(reverse("api:book-list"), {"search": "Python"})
        self.assertEqual(response.status_code, 200)
        titles = [book["title"] for book in response.data["results"]]
        self.assertEqual(titles, ["流畅的 Python"])

        response = self.client.get(reverse("api:book-list"), {"search": "Willison"})
        titles = [book["title"] for book in response.data["results"]]
        self.assertEqual(titles, ["Django 之道"])

    def test_filter_by_author(self):
        response = self.client.get(reverse("api:book-list"), {"author": self.author.pk})
        self.assertEqual(response.status_code, 200)
        titles = [book["title"] for book in response.data["results"]]
        self.assertEqual(titles, ["Django 之道"])

    def test_page_size_param(self):
        response = self.client.get(reverse("api:book-list"), {"page_size": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["count"], 2)

    def test_stats_action_aggregates(self):
        response = self.client.get(reverse("api:book-stats"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["available"], 2)

    def test_borrow_requires_authentication(self):
        response = self.client.post(reverse("api:book-borrow", kwargs={"slug": self.book.slug}))
        self.assertIn(response.status_code, (401, 403))

    def test_borrow_reuses_form_validation(self):
        self.client.force_login(self.user)
        url = reverse("api:book-borrow", kwargs={"slug": self.book.slug})
        response = self.client.post(
            url, {"due_date": (date.today() + timedelta(days=7)).isoformat()}
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.book.refresh_from_db()
        self.assertEqual(self.book.status, Status.BORROWED)

    def test_loans_viewset_requires_login(self):
        response = self.client.get(reverse("api:loan-list"))
        self.assertIn(response.status_code, (401, 403))
        self.client.force_login(self.user)
        response = self.client.get(reverse("api:loan-list"))
        self.assertEqual(response.status_code, 200)


class LoanReturnApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client = APIClient()
        cls.reader = User.objects.create_user("reader", password="reader1234")
        cls.other = User.objects.create_user("other", password="other1234")
        cls.book = Book.objects.create(
            title="数据密集型应用系统设计",
            slug="ddia",
            author=Author.objects.create(first_name="Martin", last_name="Kleppmann"),
            price="128.00",
            status=Status.AVAILABLE,
        )
        cls.record = cls.book.borrows.create(
            borrower=cls.reader, due_date=date.today() + timedelta(days=10)
        )

    def test_return_own_loan_syncs_status(self):
        self.client.force_login(self.reader)
        url = reverse("api:loan-return", kwargs={"pk": self.record.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.record.refresh_from_db()
        self.assertIsNotNone(self.record.returned_at)
        # 信号自动同步：图书状态回到可借
        self.book.refresh_from_db()
        self.assertEqual(self.book.status, Status.AVAILABLE)

    def test_cannot_return_others_loan(self):
        self.client.force_login(self.other)
        url = reverse("api:loan-return", kwargs={"pk": self.record.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)


class AuthApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user("reader", password="reader1234")

    def test_login_me_logout_flow(self):
        # 未登录：me 返回 authenticated=false（前端启动探测端点）
        response = self.client.get(reverse("api:auth-me"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["authenticated"])

        # 错误密码
        response = self.client.post(
            reverse("api:auth-login"), {"username": "reader", "password": "wrong"}
        )
        self.assertEqual(response.status_code, 400)

        # 登录成功
        response = self.client.post(
            reverse("api:auth-login"), {"username": "reader", "password": "reader1234"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "reader")

        # 已登录的 me
        response = self.client.get(reverse("api:auth-me"))
        self.assertTrue(response.data["authenticated"])

        # 登出后回到匿名态
        response = self.client.post(reverse("api:auth-logout"))
        self.assertEqual(response.status_code, 204)
        response = self.client.get(reverse("api:auth-me"))
        self.assertFalse(response.data["authenticated"])

    def test_me_sets_csrf_cookie(self):
        response = self.client.get(reverse("api:auth-me"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)
