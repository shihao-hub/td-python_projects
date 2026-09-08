"""两种 ORM 的「一致性测试」。

为什么是 TransactionTestCase 而不是 TestCase？
Django 的 TestCase 把测试数据包在一个未提交的事务里回滚 —— 而 SQLAlchemy
走的是自己的连接池、自己的事务，**看不见 Django 未提交的数据**（反之亦然）。
TransactionTestCase 让数据真实提交，两个 ORM 才能读到同一份世界。
这个坑本身就是「共库不共连接」最好的教材，见 TransactionVisibilityLessonTests。
"""

import asyncio
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.test import TransactionTestCase
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from catalog.models import Author, Book, BookGenre, BorrowRecord, Genre, Status
from sqla_lab import async_queries, queries
from sqla_lab.orm import Book as SaBook
from sqla_lab.orm import Status as SaStatus
from sqla_lab.queries import BOOK_ORDER
from sqla_lab.session import async_session_scope, dispose_engines, session_factory

User = get_user_model()


def seed() -> tuple[Book, Book, Book]:
    """造一小套覆盖各状态的数据：可借 / 在借 / 在借且逾期 / 零书作者。"""
    author = Author.objects.create(first_name="露", last_name="西")
    Author.objects.create(first_name="白", last_name="板")  # 零书作者：outerjoin 场景
    genre = Genre.objects.create(name="数据库")

    def add_book(title: str, slug: str, status: str) -> Book:
        book = Book.objects.create(
            title=title, slug=slug, author=author, price=Decimal("50.00"), status=status
        )
        BookGenre.objects.create(book=book, genre=genre)
        return book

    available = add_book("SQLAlchemy 入门", "sqla-intro", Status.AVAILABLE)
    borrowed = add_book("Django 之内", "inside-django", Status.BORROWED)
    overdue = add_book("逾期大全", "overdue-book", Status.BORROWED)
    return available, borrowed, overdue


class ReadParityTests(TransactionTestCase):
    """同一份数据（已提交），两边查询结果必须逐项一致。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 关掉 SA 连接池，否则 Windows 下测试库文件被句柄占着删不掉
        cls.addClassCleanup(dispose_engines)

    def setUp(self):
        self.reader = User.objects.create_user("reader", password="x")
        self.available, self.borrowed, self.overdue = seed()
        self.active_record = BorrowRecord.objects.create(
            book=self.borrowed,
            borrower=self.reader,
            due_date=date.today() + timedelta(days=7),
        )
        self.overdue_record = BorrowRecord.objects.create(
            book=self.overdue,
            borrower=self.reader,
            due_date=date.today() - timedelta(days=1),  # 昨天到期未还 → 逾期
        )

    def test_available_titles(self):
        with session_factory() as session:
            self.assertListEqual(
                list(Book.objects.available().values_list("title", flat=True)),
                queries.available_titles(session),
            )

    def test_with_relations(self):
        dj = [
            (b.title, b.author.full_name, tuple(g.name for g in b.genres.all()))
            for b in Book.objects.with_relations()
        ]
        with session_factory() as session:
            sa = [
                (b.title, b.author.full_name, tuple(g.name for g in b.genres))
                for b in queries.with_relations(session)
            ]
        self.assertListEqual(dj, sa)

    def test_search_cross_table(self):
        # 命中作者姓「露」（跨表 OR 条件）与书名
        with session_factory() as session:
            self.assertListEqual(
                [b.title for b in Book.objects.search("露")],
                [b.title for b in queries.search(session, "露")],
            )

    def test_author_book_counts_keep_empty_author(self):
        # Django 6.x 聚合查询不套用 Meta.ordering，两边都显式排序才可比
        dj = [
            (a.first_name, a.last_name, a.book_count)
            for a in Author.objects.annotate(book_count=Count("books")).order_by(
                "last_name", "first_name"
            )
        ]
        with session_factory() as session:
            self.assertListEqual(dj, queries.author_book_counts(session))

    def test_dashboard_stats(self):
        with session_factory() as session:
            self.assertDictEqual(
                {
                    "total_books": Book.objects.count(),
                    "available_books": Book.objects.available().count(),
                    "active_loans": BorrowRecord.objects.filter(returned_at__isnull=True).count(),
                    "total_authors": Author.objects.count(),
                },
                queries.dashboard_stats(session),
            )

    def test_overdue_records(self):
        with session_factory() as session:
            sa = [r.id for r in queries.overdue_records(session)]
        dj = list(
            BorrowRecord.objects.filter(
                returned_at__isnull=True, due_date__lt=date.today()
            ).values_list("pk", flat=True)
        )
        self.assertListEqual(dj, sa)
        self.assertIn(self.overdue_record.pk, sa)

    def test_hybrid_property_in_query(self):
        with session_factory() as session:
            hybrid_titles = list(
                session.scalars(
                    select(SaBook.title).where(SaBook.is_borrowable).order_by(*BOOK_ORDER)
                )
            )
        self.assertListEqual(
            list(Book.objects.filter(status=Status.AVAILABLE).values_list("title", flat=True)),
            hybrid_titles,
        )

    def test_status_roundtrip_is_enum(self):
        # Enum(native_enum=False, values_callable=...) 让读回来的就是枚举成员
        with session_factory() as session:
            book = session.scalars(select(SaBook).where(SaBook.slug == "sqla-intro")).one()
            self.assertEqual(book.status, SaStatus.AVAILABLE)
            self.assertIsInstance(book.status, SaStatus)

    def test_async_parity(self):
        async def pair() -> tuple[dict[str, int], dict[str, int]]:
            dj = {
                "total_books": await Book.objects.acount(),
                "available_books": await Book.objects.available().acount(),
                "active_loans": await BorrowRecord.objects.filter(
                    returned_at__isnull=True
                ).acount(),
                "total_authors": await Author.objects.acount(),
            }
            async with async_session_scope() as session:
                return dj, await async_queries.dashboard_stats(session)

        dj, sa = asyncio.run(pair())
        self.assertDictEqual(dj, sa)


class WritePathTests(TransactionTestCase):
    """写路径：跨 ORM 互见、Unit of Work、同一个部分唯一索引兜底两边。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 关掉 SA 连接池，否则 Windows 下测试库文件被句柄占着删不掉
        cls.addClassCleanup(dispose_engines)

    def setUp(self):
        self.reader = User.objects.create_user("reader", password="x")
        self.available, self.borrowed, self.overdue = seed()
        BorrowRecord.objects.create(
            book=self.borrowed,
            borrower=self.reader,
            due_date=date.today() + timedelta(days=7),
        )

    def test_sqlalchemy_write_visible_to_django_after_commit(self):
        with session_factory() as session:
            record = queries.borrow_book(
                session, self.available.pk, self.reader.pk, date.today() + timedelta(days=3)
            )
            self.assertIsNotNone(record)
        # SA 已提交：Django 立刻可见（跨 ORM 写读）
        assert record is not None
        dj_record = BorrowRecord.objects.get(pk=record.id)
        self.assertEqual(dj_record.book_id, self.available.pk)

    def test_partial_unique_index_rejects_sqlalchemy_too(self):
        with session_factory() as session:
            first = queries.borrow_book(session, self.available.pk, self.reader.pk, date.today())
            self.assertIsNotNone(first)
            # 同一本书再借：数据库条件唯一索引对 SQLAlchemy 同样生效
            second = queries.borrow_book(session, self.available.pk, self.reader.pk, date.today())
            self.assertIsNone(second)

    def test_partial_unique_index_rejects_django_when_sqlalchemy_holds_it(self):
        with session_factory() as session:
            held = queries.borrow_book(session, self.available.pk, self.reader.pk, date.today())
            self.assertIsNotNone(held)  # SA 已提交在借记录
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BorrowRecord.objects.create(
                    book=self.available, borrower=self.reader, due_date=date.today()
                )

    def test_mark_returned_update_statement(self):
        with session_factory() as session:
            record = queries.borrow_book(
                session, self.available.pk, self.reader.pk, date.today() + timedelta(days=3)
            )
            assert record is not None
            returned_at = queries.mark_returned(session, record.id)
            session.commit()
        self.assertIsNotNone(returned_at)
        dj_record = BorrowRecord.objects.get(pk=record.id)
        self.assertIsNotNone(dj_record.returned_at)


class TransactionVisibilityLessonTests(TransactionTestCase):
    """「共库不共连接」的活教材：未提交的数据，另一个 ORM 看不见。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 关掉 SA 连接池，否则 Windows 下测试库文件被句柄占着删不掉
        cls.addClassCleanup(dispose_engines)

    def test_uncommitted_django_data_invisible_to_sqlalchemy(self):
        author, _ = Author.objects.get_or_create(first_name="测", last_name="试")
        count_sql = select(func.count()).select_from(SaBook)
        total_before = Book.objects.count()
        with transaction.atomic():
            Book.objects.create(
                title="未提交的书",
                slug="in-flight",
                author=author,
                price=Decimal("1.00"),
            )
            with session_factory() as session:
                # Django 事务未提交，SA 自己的连接「看不见」的方式取决于后端：
                # - 文件 SQLite / PostgreSQL：读旧快照 → 仍是 total_before；
                # - 共享内存库（Django 测试默认 cache=shared）：表锁直接冲突 → OperationalError。
                # 两种都是数据库隔离性的体现 —— 没有任何一个能读到未提交的行
                try:
                    self.assertEqual(session.execute(count_sql).scalar(), total_before)
                except OperationalError:
                    pass
        # atomic 退出（提交）后，两边才看到同一个世界
        with session_factory() as session:
            self.assertEqual(session.execute(count_sql).scalar(), total_before + 1)
