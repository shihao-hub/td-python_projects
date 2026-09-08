"""对照场景注册表：每条场景 = 同一业务问题 + Django 写法 + SQLAlchemy 写法。

orm_compare 命令执行全部场景（读 + 写，写路径在事务里回滚不落库）；
/compare/ 页面只执行读场景，写场景展示代码对照。
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef
from django.utils import timezone
from sqlalchemy import exists, func, select, update

from catalog.models import Author, Book, BorrowRecord
from catalog.models import Status as DjStatus

from . import queries
from .orm import Book as SaBook
from .orm import BorrowRecord as SaBorrow
from .orm import LabUser, Status
from .queries import BOOK_ORDER

DUE = date.today() + timedelta(days=7)


def _dj_available_titles() -> list[str]:
    # BookQuerySet.available()：自定义 QuerySet 方法 + Meta.ordering 默认排序
    return list(Book.objects.available().values_list("title", flat=True))


def _dj_with_relations() -> list[tuple[str, str, tuple[str, ...]]]:
    return [
        (b.title, b.author.full_name, tuple(g.name for g in b.genres.all()))
        for b in Book.objects.with_relations()
    ]


def _dj_search() -> list[str]:
    return [b.title for b in Book.objects.search("数据")]


def _dj_author_counts() -> list[tuple[str, str, int]]:
    # 教学点：Django 6.x 的聚合查询不再套用 Meta.ordering（GROUP BY 语义歧义），
    # 显式 order_by 才和 SQLAlchemy 侧的显式排序对得上
    return [
        (a.first_name, a.last_name, a.book_count)
        for a in Author.objects.annotate(book_count=Count("books")).order_by(
            "last_name", "first_name"
        )
    ]


def _dj_dashboard() -> dict[str, int]:
    # 与 dashboard 视图同款统计（那里是 await acount()，这里同步孪生）
    return {
        "total_books": Book.objects.count(),
        "available_books": Book.objects.available().count(),
        "active_loans": BorrowRecord.objects.filter(returned_at__isnull=True).count(),
        "total_authors": Author.objects.count(),
    }


def _dj_latest() -> list[str]:
    return [b.title for b in Book.objects.with_relations().order_by("-created_at", "title")[:5]]


def _dj_overdue() -> list[int]:
    return list(
        BorrowRecord.objects.filter(
            returned_at__isnull=True, due_date__lt=timezone.localdate()
        ).values_list("pk", flat=True)
    )


def _dj_hybrid() -> list[str]:
    # Django：property 不能进查询 —— is_borrowable 的查询版必须手写字段条件
    return list(Book.objects.filter(status=DjStatus.AVAILABLE).values_list("title", flat=True))


def _sa_hybrid_titles(session: Any) -> list[str]:
    stmt = select(SaBook.title).where(SaBook.is_borrowable).order_by(*BOOK_ORDER)
    return list(session.scalars(stmt))


def _dj_borrow_and_return() -> tuple[bool, int]:
    # 教学点：排除「有未归还记录的书」不能用 exclude(borrows__returned_at__isnull=True)——
    # 反向关系上 IS NULL 会连「从没被借过的书」（LEFT JOIN 空行）一起匹配掉；
    # Exists + OuterRef 才是精确语义，且与 SQLAlchemy 的 ~exists() 完全同构
    active_borrow = BorrowRecord.objects.filter(book=OuterRef("pk"), returned_at__isnull=True)
    book = (
        Book.objects.available()
        .filter(~Exists(active_borrow))
        .order_by("-created_at", "title")
        .first()
    )
    assert book is not None
    from django.contrib.auth import get_user_model

    reader = get_user_model().objects.get(username="reader")
    with transaction.atomic():
        record = BorrowRecord.objects.create(book=book, borrower=reader, due_date=DUE)
        record.mark_returned()  # save(update_fields=["returned_at"])
        result = (record.returned_at is not None, book.pk)
        transaction.set_rollback(True)  # 演示完回滚，不落库
    return result


def _sa_borrow_and_return(session: Any) -> tuple[bool, int]:
    stmt = (
        select(SaBook.id)
        .where(
            SaBook.status == Status.AVAILABLE,
            ~exists()
            .where(SaBorrow.book_id == SaBook.id, SaBorrow.returned_at.is_(None))
            .correlate(SaBook),
        )
        .order_by(*BOOK_ORDER)
        .limit(1)
    )
    book_id = session.scalars(stmt).first()
    assert book_id is not None
    reader_id = session.scalars(select(LabUser.id).where(LabUser.username == "reader")).one()
    session.add(SaBorrow(book_id=book_id, borrower_id=reader_id, due_date=DUE))
    session.flush()  # Unit of Work：此刻发 INSERT，事务仍握在手里
    returned_at = session.execute(
        update(SaBorrow)
        .where(SaBorrow.book_id == book_id, SaBorrow.returned_at.is_(None))
        .values(returned_at=func.now())
        .returning(SaBorrow.returned_at)
    ).scalar()
    result = (returned_at is not None, book_id)
    session.rollback()  # 与 Django set_rollback 对应：演示完回滚
    return result


def _dj_unique_guard() -> bool:
    ddia = Book.objects.get(slug="ddia")
    from django.contrib.auth import get_user_model

    reader = get_user_model().objects.get(username="reader")
    try:
        with transaction.atomic():  # savepoint：IntegrityError 不污染外层
            BorrowRecord.objects.create(book=ddia, borrower=reader, due_date=DUE)
    except IntegrityError:
        return True  # 数据库部分唯一索引兜住了并发/重复借阅
    return False


def _sa_unique_guard(session: Any) -> bool:
    book_id = session.scalars(select(SaBook.id).where(SaBook.slug == "ddia")).one()
    reader_id = session.scalars(select(LabUser.id).where(LabUser.username == "reader")).one()
    record = queries.borrow_book(session, book_id, reader_id, DUE)  # None = 被索引拒绝
    session.rollback()
    return record is None


def fmt_value(value: Any) -> str:
    """结果 → 页面/命令行展示文本。"""
    if isinstance(value, dict):
        return " ".join(f"{k}={v}" for k, v in value.items())
    if isinstance(value, list) and len(value) > 5:
        return f"[{', '.join(fmt_value(v) for v in value[:5])}, … 共 {len(value)} 项]"
    return str(value)


@dataclass
class Scenario:
    no: int
    name: str
    mapping: str  # 一行说清「Django 概念 ↔ SQLAlchemy 概念」
    dj_code: str
    sa_code: str
    run_dj: Callable[[], Any]
    run_sa: Callable[[Any], Any]
    write: bool = False  # 写场景：命令执行（回滚），页面仅展示
    render: Callable[[Any], str] = fmt_value


READ_SCENARIOS: list[Scenario] = [
    Scenario(
        no=1,
        name="可借图书",
        mapping="QuerySet.filter ↔ select().where()",
        dj_code=('Book.objects.available()\n    .values_list("title", flat=True)'),
        sa_code=(
            "select(Book.title)\n"
            "    .where(Book.status == Status.AVAILABLE)\n"
            "    .order_by(Book.created_at.desc(), Book.title)"
        ),
        run_dj=_dj_available_titles,
        run_sa=lambda s: queries.available_titles(s),
    ),
    Scenario(
        no=2,
        name="关联加载（N+1 克星）",
        mapping="select_related ↔ joinedload；prefetch_related ↔ selectinload",
        dj_code=(
            "Book.objects.with_relations()\n"
            '# 内部 = select_related("author").prefetch_related("genres")'
        ),
        sa_code=(
            "select(Book)\n"
            "    .options(\n"
            "        joinedload(Book.author),\n"
            "        selectinload(Book.genres),\n"
            "    )"
        ),
        run_dj=_dj_with_relations,
        run_sa=lambda s: [
            (b.title, b.author.full_name, tuple(g.name for g in b.genres))
            for b in queries.with_relations(s)
        ],
    ),
    Scenario(
        no=3,
        name="跨表搜索",
        mapping="Q | Q + __icontains ↔ or_() + .ilike() + join",
        dj_code=(
            "Book.objects.search('数据')\n"
            "# Q(title__icontains=kw) | Q(summary__icontains=kw)\n"
            "# | Q(author__first_name__icontains=kw)\n"
            "# | Q(author__last_name__icontains=kw)"
        ),
        sa_code=(
            "select(Book).join(Author).where(or_(\n"
            "    Book.title.ilike(f'%{kw}%'),\n"
            "    Book.summary.ilike(f'%{kw}%'),\n"
            "    Author.first_name.ilike(f'%{kw}%'),\n"
            "    Author.last_name.ilike(f'%{kw}%'),\n"
            "))"
        ),
        run_dj=_dj_search,
        run_sa=lambda s: [b.title for b in queries.search(s, "数据")],
    ),
    Scenario(
        no=4,
        name="作者图书数（聚合）",
        mapping="annotate(Count) 自动 JOIN ↔ outerjoin + group_by 全显式",
        dj_code=(
            "Author.objects.annotate(\n"
            '    book_count=Count("books")\n'
            ")  # 自动 LEFT OUTER JOIN + GROUP BY"
        ),
        sa_code=(
            "select(Author.first_name, Author.last_name,\n"
            "       func.count(Book.id))\n"
            "    .outerjoin(Book, Book.author_id == Author.id)\n"
            "    .group_by(Author.id, Author.first_name, Author.last_name)"
        ),
        run_dj=_dj_author_counts,
        run_sa=lambda s: queries.author_book_counts(s),
    ),
    Scenario(
        no=5,
        name="看板统计",
        mapping="qs.count() ↔ select(func.count()).select_from()",
        dj_code=(
            "Book.objects.count()\n"
            "Book.objects.available().count()\n"
            "BorrowRecord.objects.filter(returned_at__isnull=True).count()\n"
            "Author.objects.count()"
        ),
        sa_code=(
            "select(func.count()).select_from(Book)\n"
            "... .where(Book.status == Status.AVAILABLE)\n"
            "... .where(BorrowRecord.returned_at.is_(None))\n"
            "select(func.count()).select_from(Author)"
        ),
        run_dj=_dj_dashboard,
        run_sa=lambda s: queries.dashboard_stats(s),
    ),
    Scenario(
        no=6,
        name="最新 5 本",
        mapping="切片 qs[:5] ↔ .limit(5)",
        dj_code=('Book.objects.with_relations()\n    .order_by("-created_at", "title")[:5]'),
        sa_code=(
            "select(Book)\n"
            "    .options(joinedload(Book.author), selectinload(Book.genres))\n"
            "    .order_by(Book.created_at.desc(), Book.title)\n"
            "    .limit(5)"
        ),
        run_dj=_dj_latest,
        run_sa=lambda s: [b.title for b in queries.latest_books(s)],
    ),
    Scenario(
        no=7,
        name="逾期借阅",
        mapping="filter(isnull=True, lt=today) ↔ .is_(None) + 日期绑定",
        dj_code=(
            "BorrowRecord.objects.filter(\n"
            "    returned_at__isnull=True,\n"
            "    due_date__lt=timezone.localdate(),\n"
            ")"
        ),
        sa_code=(
            "select(BorrowRecord).where(\n"
            "    BorrowRecord.returned_at.is_(None),\n"
            "    BorrowRecord.due_date < today,  # Python 侧日期\n"
            ")"
        ),
        run_dj=_dj_overdue,
        run_sa=lambda s: [r.id for r in queries.overdue_records(s)],
    ),
    Scenario(
        no=8,
        name="hybrid_property 两用",
        mapping="Django property 不能进查询 ↔ hybrid 一个定义实例/查询两用",
        dj_code=(
            "# is_borrowable 是 property，查询里用不了，得手写：\n"
            "Book.objects.filter(status=Status.AVAILABLE)"
        ),
        sa_code=(
            "# 同一个 hybrid，实例用 book.is_borrowable，查询用：\n"
            "select(Book).where(Book.is_borrowable)"
        ),
        run_dj=_dj_hybrid,
        run_sa=_sa_hybrid_titles,
    ),
]

WRITE_SCENARIOS: list[Scenario] = [
    Scenario(
        no=9,
        name="借阅 → 归还（事务演示）",
        mapping="objects.create + save(update_fields) ↔ add+flush + update() 语句",
        dj_code=(
            "with transaction.atomic():\n"
            "    record = BorrowRecord.objects.create(...)\n"
            "    record.mark_returned()  # save(update_fields=[...])\n"
            "    transaction.set_rollback(True)"
        ),
        sa_code=(
            "session.add(BorrowRecord(...))\n"
            "session.flush()          # INSERT，事务仍在手\n"
            "session.execute(update(BorrowRecord)\n"
            "    .where(...).values(returned_at=func.now()))\n"
            "session.rollback()       # 与 set_rollback 对应"
        ),
        run_dj=_dj_borrow_and_return,
        run_sa=_sa_borrow_and_return,
        write=True,
    ),
    Scenario(
        no=10,
        name="部分唯一索引兜底",
        mapping=("IntegrityError（Django）↔ IntegrityError（SQLAlchemy）—— 同一个索引在兜底"),
        dj_code=(
            "with transaction.atomic():\n"
            "    BorrowRecord.objects.create(book=ddia, ...)  # 已在借\n"
            "# → IntegrityError（数据库条件唯一索引拒绝）"
        ),
        sa_code=(
            "session.add(BorrowRecord(book_id=ddia_id, ...))\n"
            "session.commit()\n"
            "# → IntegrityError：unique_active_borrow_per_book"
        ),
        run_dj=_dj_unique_guard,
        run_sa=_sa_unique_guard,
        write=True,
    ),
]

ALL_SCENARIOS = READ_SCENARIOS + WRITE_SCENARIOS
