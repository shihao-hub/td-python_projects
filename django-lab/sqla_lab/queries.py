"""同步查询对照：catalog 里的每条 Django ORM 用法，在这里都有一个 SQLAlchemy 翻译。

对照总表（查询语法）：
- QuerySet.filter(status=...)          ↔ select(...).where(Book.status == ...)
- Q(a) | Q(b)                          ↔ or_(...)；Q(a) & Q(b) ↔ and_(...)
- __icontains                          ↔ .ilike(f"%{kw}%")
- select_related("author")             ↔ options(joinedload(Book.author))
- prefetch_related("genres")           ↔ options(selectinload(Book.genres))
- annotate(book_count=Count("books"))  ↔ func.count + outerjoin + group_by（全显式）
- Model.objects.count()                ↔ select(func.count()).select_from(Model)
- qs.first()                           ↔ ...limit(1)
- Meta.ordering                        ↔ 必须显式 order_by(...)（SQLAlchemy 无模型级默认排序）
- Model.objects.create(...)            ↔ session.add(Model(...)) + commit
- obj.save(update_fields=[...])        ↔ session.execute(update(Model).where(...).values(...))
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from .orm import Author, Book, BorrowRecord, Genre, LabUser, Status

# 对照 Meta.ordering = ["-created_at", "title"]：
# Django 把默认排序写在模型上，SQLAlchemy 要求每次查询显式声明 —— 少一分魔法，多一分直白
BOOK_ORDER: tuple[Any, ...] = (Book.created_at.desc(), Book.title)
BORROW_ORDER = (BorrowRecord.borrowed_at.desc(),)


def available_titles(session: Session) -> list[str]:
    """对照 `Book.objects.available().values_list("title", flat=True)`。

    filter(status=...) ↔ where(Book.status == ...)；Django 的 available()
    是自定义 QuerySet 方法，SQLAlchemy 侧就是普通函数 —— 组合能力靠函数复用而非继承。
    """
    stmt = select(Book.title).where(Book.status == Status.AVAILABLE).order_by(*BOOK_ORDER)
    return list(session.scalars(stmt))


def with_relations(session: Session) -> list[Book]:
    """对照 `Book.objects.with_relations()`（select_related + prefetch_related）。

    joinedload：LEFT JOIN 一条 SQL 取回作者（对照 select_related 的 JOIN 策略）；
    selectinload：第二条 SELECT ... WHERE id IN (...) 取回分类（对照 prefetch_related）。
    懒加载会在访问属性时炸出 N+1 —— 两边都用 eager loading 关掉它。
    """
    stmt = (
        select(Book)
        .options(joinedload(Book.author), selectinload(Book.genres))
        .order_by(*BOOK_ORDER)
    )
    return list(session.scalars(stmt))


def search(session: Session, keywords: str) -> list[Book]:
    """对照 `Book.objects.search(kw)`：Q | Q 跨表 OR ↔ or_() + join。

    icontains 在 SQLite 是 LIKE（大小写不敏感是 ASCII 行为），在 PG 是 ILIKE ——
    SQLAlchemy 的 .ilike() 两边后端语义一致，可移植性更好。
    """
    pattern = f"%{keywords}%"
    stmt = (
        select(Book)
        .join(Author)
        .where(
            or_(
                Book.title.ilike(pattern),
                Book.summary.ilike(pattern),
                Author.first_name.ilike(pattern),
                Author.last_name.ilike(pattern),
            )
        )
        .options(joinedload(Book.author), selectinload(Book.genres))
        .order_by(*BOOK_ORDER)
    )
    return list(session.scalars(stmt))


def author_book_counts(session: Session) -> list[tuple[str, str, int]]:
    """对照 `Author.objects.annotate(book_count=Count("books"))`。

    Django 的 annotate 会自动生成 LEFT OUTER JOIN + GROUP BY；
    SQLAlchemy 里 JOIN / GROUP BY 全部摆在台面上 —— 同一条 SQL，两种透明度。
    返回 (名, 姓, 图书数)，零本书的作者也保留（outerjoin 的语义）。
    """
    stmt = (
        select(Author.first_name, Author.last_name, func.count(Book.id).label("book_count"))
        .outerjoin(Book, Book.author_id == Author.id)
        .group_by(Author.id, Author.first_name, Author.last_name)
        .order_by(Author.last_name, Author.first_name)
    )
    return [(first, last, count) for first, last, count in session.execute(stmt)]


def dashboard_stats(session: Session) -> dict[str, int]:
    """对照 dashboard 视图里的 acount 系列（异步版的同步孪生）。

    count() ↔ select(func.count()).select_from(...)；
    filter(returned_at__isnull=True) ↔ where(BorrowRecord.returned_at.is_(None))。
    Django 四条 QuerySet 链；SQLAlchemy 四条 select —— 各自独立发 SQL，行为等价。
    """
    return {
        "total_books": session.scalar(select(func.count()).select_from(Book)) or 0,
        "available_books": session.scalar(
            select(func.count()).select_from(Book).where(Book.status == Status.AVAILABLE)
        )
        or 0,
        "active_loans": session.scalar(
            select(func.count()).select_from(BorrowRecord).where(BorrowRecord.returned_at.is_(None))
        )
        or 0,
        "total_authors": session.scalar(select(func.count()).select_from(Author)) or 0,
    }


def latest_books(session: Session, limit: int = 5) -> list[Book]:
    """对照 `Book.objects.with_relations().order_by("-created_at")[:5]`。

    切片 [:5] ↔ .limit(5)。
    """
    stmt = (
        select(Book)
        .options(joinedload(Book.author), selectinload(Book.genres))
        .order_by(*BOOK_ORDER)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def active_borrow_of(session: Session, book_id: int) -> BorrowRecord | None:
    """对照 Book.active_borrow：`self.borrows.filter(returned_at__isnull=True).first()`。

    related_name 集合筛选 ↔ 反向走 join 的 select；first() ↔ LIMIT 1。
    """
    stmt = (
        select(BorrowRecord)
        .where(BorrowRecord.book_id == book_id, BorrowRecord.returned_at.is_(None))
        .limit(1)
    )
    return session.scalars(stmt).first()


def overdue_records(session: Session, today: date | None = None) -> list[BorrowRecord]:
    """对照遍历借阅记录过滤 `record.is_overdue`（Django：property + Python 过滤）。

    hybrid_property 的杀手锏：where(BorrowRecord.is_overdue) 在 SQL 里完成
    「未归还 AND 到期日 < 今天」的判断 —— Django 需要 property(实例) 或
    Q(returned_at__isnull=True, due_date__lt=today)(查询) 两套写法，这里一套。

    时区坑：这里绑定 Python 侧日期而非 func.current_date() —— SQLite 的
    CURRENT_DATE 是 UTC，而 Django 的 timezone.localdate() 用 TIME_ZONE 设置
    （本项目 Asia/Shanghai），两者在 UTC 0 点~8 点之间会得出不同日期。
    """
    stmt = select(BorrowRecord).where(
        BorrowRecord.returned_at.is_(None),
        BorrowRecord.due_date < (today or date.today()),
    )
    return list(session.scalars(stmt))


def borrow_book(
    session: Session, book_id: int, borrower_id: int, due_date: date
) -> BorrowRecord | None:
    """对照 `BorrowRecord.objects.create(book=book, borrower=user, due_date=...)`。

    Unit of Work：session.add() 只是登记，flush/commit 时才发 INSERT。
    部分唯一索引（unique_active_borrow_per_book）由数据库兜底并发 ——
    同一本书已在借时第二次 INSERT 抛 IntegrityError，与 Django 侧如出一辙。
    """
    session.add(BorrowRecord(book_id=book_id, borrower_id=borrower_id, due_date=due_date))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return None
    record = active_borrow_of(session, book_id)
    assert record is not None
    return record


def mark_returned(session: Session, record_id: int) -> datetime | None:
    """对照 `record.mark_returned()`：save(update_fields=["returned_at"])。

    SQLAlchemy 的 update() 是「一条 UPDATE 语句走起」，不先 SELECT 加载对象 ——
    对照 Django 的 queryset.update()；Django 的 save(update_fields) 则是先有对象再按字段 UPDATE。
    返回归还时间；已在借记录不存在时返回 None。
    """
    stmt = (
        update(BorrowRecord)
        .where(BorrowRecord.id == record_id, BorrowRecord.returned_at.is_(None))
        .values(returned_at=func.now())
        .returning(BorrowRecord.returned_at)
    )
    return session.scalar(stmt)


def genre_names_of(session: Session, book: Book) -> list[str]:
    """对照 `book.genres.all()`（M2M 遍历）：viewonly relationship 直接迭代。"""
    return [genre.name for genre in book.genres]


def books_by_username(session: Session, username: str) -> list[str]:
    """对照 `User.borrow_records.all()`（反向 related_name）：跨表 join 演示。"""
    stmt = (
        select(Book.title)
        .join(BorrowRecord, BorrowRecord.book_id == Book.id)
        .join(LabUser, BorrowRecord.borrower_id == LabUser.id)
        .where(LabUser.username == username)
        .order_by(Book.title)
    )
    return list(session.scalars(stmt))


def all_genre_names(session: Session) -> list[str]:
    """对照 `Genre.objects.all()`（Meta.ordering=["name"] 在这里也得显式写）。"""
    stmt = select(Genre.name).order_by(Genre.name)
    return list(session.scalars(stmt))


def book_by_slug(session: Session, slug: str) -> Book | None:
    """对照 `Book.objects.with_relations().get(slug=...)`：
    DoesNotExist ↔ scalar() 返回 None；单条也要 eager loading。"""
    stmt = (
        select(Book)
        .where(Book.slug == slug)
        .options(joinedload(Book.author), selectinload(Book.genres))
    )
    return session.scalar(stmt)
