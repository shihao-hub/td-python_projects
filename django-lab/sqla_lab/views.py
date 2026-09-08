"""对照页面：同一个业务，Django ORM 与 SQLAlchemy 各写一遍，结果并排呈现。

页面只执行读场景（写场景看代码对照，动手跑请用 orm_compare 命令 ——
它会把写路径放进事务里回滚，页面不碰写操作，永远安全）。
"""

import asyncio

from django.contrib.auth.decorators import login_not_required
from django.shortcuts import render

from catalog.models import Author, Book, BorrowRecord

from . import async_queries
from .scenarios import READ_SCENARIOS, WRITE_SCENARIOS, fmt_value
from .session import async_session_scope, session_factory


async def _async_stats_pair() -> tuple[dict[str, int], dict[str, int]]:
    """dashboard 视图（Django async ORM）× AsyncSession 的看板统计对照。"""
    dj = {
        "total_books": await Book.objects.acount(),
        "available_books": await Book.objects.available().acount(),
        "active_loans": await BorrowRecord.objects.filter(returned_at__isnull=True).acount(),
        "total_authors": await Author.objects.acount(),
    }
    async with async_session_scope() as session:
        sa = await async_queries.dashboard_stats(session)
    return dj, sa


@login_not_required
def compare(request):
    rows = []
    for sc in READ_SCENARIOS:
        dj_result = sc.run_dj()
        with session_factory() as session:
            sa_result = sc.run_sa(session)
            session.rollback()  # 防御性回滚：页面永不留下写痕迹
        rows.append(
            {
                "no": sc.no,
                "name": sc.name,
                "mapping": sc.mapping,
                "dj_code": sc.dj_code,
                "sa_code": sc.sa_code,
                "dj_result": fmt_value(dj_result),
                "sa_result": fmt_value(sa_result),
                "match": dj_result == sa_result,
            }
        )

    dj_async, sa_async = asyncio.run(_async_stats_pair())
    async_row = {
        "dj_result": fmt_value(dj_async),
        "sa_result": fmt_value(sa_async),
        "match": dj_async == sa_async,
    }
    total = len(rows) + 1
    matched = sum(row["match"] for row in rows) + async_row["match"]

    return render(
        request,
        "sqla_lab/compare.html",
        {
            "rows": rows,
            "write_rows": WRITE_SCENARIOS,
            "async_row": async_row,
            "matched": matched,
            "total": total,
        },
    )
