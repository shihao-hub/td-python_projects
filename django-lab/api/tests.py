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

    def test_anonymous_can_read_list_and_detail(self):
        response = self.client.get(reverse("api:book-list"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.data)
        detail = self.client.get(reverse("api:book-detail", kwargs={"pk": self.book.pk}))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["title"], "Django 之道")
        self.assertEqual(detail.data["status_display"], "可借")
        self.assertTrue(detail.data["page_url"].endswith(f"/books/{self.book.slug}/"))

    def test_stats_action_aggregates(self):
        response = self.client.get(reverse("api:book-stats"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["available"], 1)

    def test_borrow_requires_authentication(self):
        response = self.client.post(reverse("api:book-borrow", kwargs={"pk": self.book.pk}))
        self.assertIn(response.status_code, (401, 403))

    def test_borrow_reuses_form_validation(self):
        self.client.force_login(self.user)
        url = reverse("api:book-borrow", kwargs={"pk": self.book.pk})
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
