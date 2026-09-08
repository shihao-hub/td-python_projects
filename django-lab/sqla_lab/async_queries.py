"""异步查询对照：dashboard 视图（Django async ORM）的 SQLAlchemy AsyncSession 翻译。

对照总表（异步语法）：
- await Model.objects.acount()          ↔ (await session.scalar(select(...))) or 0
- async for book in qs                  ↔ (await session.scalars(stmt)).all()
                                          （流式：async for row in await session.stream(stmt)）
- Django async ORM 内部把同步驱动丢进线程池 ↔ SQLAlchemy 的 async 栈是「真异步驱动」
  （aiosqlite / asyncpg），不需要线程池搬运 —— 两种路线都值得知道。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .orm import Author, Book, BorrowRecord, Status
from .queries import BOOK_ORDER


async def dashboard_stats(session: AsyncSession) -> dict[str, int]:
    """对照 `await Book.objects.acount()` 等四连 —— await 的位置从 QuerySet 换到 Session。"""
    return {
        "total_books": (await session.scalar(select(func.count()).select_from(Book))) or 0,
        "available_books": (
            await session.scalar(
                select(func.count()).select_from(Book).where(Book.status == Status.AVAILABLE)
            )
        )
        or 0,
        "active_loans": (
            await session.scalar(
                select(func.count())
                .select_from(BorrowRecord)
                .where(BorrowRecord.returned_at.is_(None))
            )
        )
        or 0,
        "total_authors": (await session.scalar(select(func.count()).select_from(Author))) or 0,
    }


async def latest_books(session: AsyncSession, limit: int = 5) -> list[Book]:
    """对照 `[book async for book in Book.objects.with_relations()[:5]]`。

    AsyncSession.scalars() 返回的是 awaitable，拿到结果集后 .all()；
    流式处理则用 `async for row in await session.stream(stmt)`。
    """
    stmt = select(Book).options(selectinload(Book.author)).order_by(*BOOK_ORDER).limit(limit)
    return list((await session.scalars(stmt)).all())
