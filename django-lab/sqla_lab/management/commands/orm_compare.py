"""orm_compare：把同一业务问题分别交给 Django ORM 和 SQLAlchemy 执行，并排对照。

用法：
    uv run manage.py orm_compare            # 跑全部场景（读 + 写路径回滚演示）+ 异步对照
    uv run manage.py orm_compare --sql      # 额外打印两边真实执行的每条 SQL

数据前提：先跑过 `uv run manage.py migrate && uv run manage.py seed_demo`。
写路径场景全程在事务里并最终回滚，命令跑完数据库不留任何痕迹（末尾有校验）。
"""

import asyncio
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from sqlalchemy import event

from catalog.models import BorrowRecord
from sqla_lab import async_queries
from sqla_lab.scenarios import ALL_SCENARIOS, fmt_value
from sqla_lab.session import get_engine, session_factory

# Windows GBK 控制台没有 ↔ ✓ ✗ 等字符，输出前按目标编码降级
_SAFE_MAP = str.maketrans({"↔": "<->", "✓": "√", "✗": "×"})


def safe(text: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        return text.translate(_SAFE_MAP)
    return text


class SaSqlCollector:
    """监听 engine 的 before_cursor_execute，收集 SQLAlchemy 真实下发的 SQL。"""

    def __init__(self) -> None:
        self.engine = get_engine()
        self.statements: list[str] = []

    def _record(self, conn, cursor, statement, parameters, context, executemany) -> None:
        params = f"  -- params: {parameters!r}" if parameters else ""
        self.statements.append(f"{statement}{params}")

    def __enter__(self) -> "SaSqlCollector":
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._record)


async def _async_pair() -> tuple[dict[str, int], dict[str, int]]:
    """Django async ORM（acount）× SQLAlchemy AsyncSession 各跑一遍看板统计。"""
    from catalog.models import Author, Book

    dj = {
        "total_books": await Book.objects.acount(),
        "available_books": await Book.objects.available().acount(),
        "active_loans": await BorrowRecord.objects.filter(returned_at__isnull=True).acount(),
        "total_authors": await Author.objects.acount(),
    }
    from sqla_lab.session import async_session_scope

    async with async_session_scope() as session:
        sa = await async_queries.dashboard_stats(session)
    return dj, sa


class Command(BaseCommand):
    help = "Django ORM × SQLAlchemy 并排对照：同一查询两种写法，验证结果与 SQL"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--sql", action="store_true", help="打印两边真实执行的 SQL")

    def handle(self, *args, **options) -> None:
        show_sql: bool = options["sql"]
        self.stdout.write(self.style.MIGRATE_HEADING("━" * 62))
        self.stdout.write(self.style.MIGRATE_HEADING(" Django ORM × SQLAlchemy 对照 · 共库同数据"))
        self.stdout.write(self.style.MIGRATE_HEADING("━" * 62))

        if not BorrowRecord.objects.filter(returned_at__isnull=True, book__slug="ddia").exists():
            self.stdout.write(
                self.style.WARNING("演示数据不完整，建议先执行 seed_demo（场景 10 依赖 ddia 在借）")
            )

        active_loans_before = BorrowRecord.objects.filter(returned_at__isnull=True).count()
        passed = 0

        for sc in ALL_SCENARIOS:
            tag = "（写路径·回滚演示）" if sc.write else ""
            with CaptureQueriesContext(connection) as dj_ctx:
                dj_result = sc.run_dj()
            with SaSqlCollector() as sa_collector, session_factory() as session:
                sa_result = sc.run_sa(session)
                session.rollback()
            ok = dj_result == sa_result
            passed += ok
            mark = self.style.SUCCESS("✓ 一致") if ok else self.style.ERROR("✗ 不一致")
            self.stdout.write(
                safe(
                    f"\n[{sc.no:>2}/{len(ALL_SCENARIOS)}] {sc.name} {tag}\n"
                    f"      {sc.mapping}\n"
                    f"      Django {len(dj_ctx.captured_queries)} SQL → {fmt_value(dj_result)}\n"
                    f"      SQLA   {len(sa_collector.statements)} SQL → "
                    f"{fmt_value(sa_result)}   {mark}"
                )
            )
            if show_sql:
                for dj_sql in dj_ctx.captured_queries:
                    self.stdout.write(self.style.HTTP_INFO(f"      [Django] {dj_sql['sql'][:160]}"))
                for sa_sql in sa_collector.statements:
                    self.stdout.write(self.style.HTTP_INFO(f"      [SQLA  ] {sa_sql[:160]}"))

        # 异步对照
        dj_async, sa_async = asyncio.run(_async_pair())
        ok_async = dj_async == sa_async
        passed += ok_async
        mark = self.style.SUCCESS("✓ 一致") if ok_async else self.style.ERROR("✗ 不一致")
        self.stdout.write(
            safe(
                f"\n[{len(ALL_SCENARIOS) + 1:>2}/{len(ALL_SCENARIOS) + 1}] 异步看板统计 "
                f"（acount ↔ AsyncSession）\n"
                f"      Django await acount() ×4 → {fmt_value(dj_async)}\n"
                f"      SQLA   AsyncSession      → {fmt_value(sa_async)}   {mark}"
            )
        )

        # 写场景回滚校验：在借数应与开跑前一致
        active_loans_after = BorrowRecord.objects.filter(returned_at__isnull=True).count()
        clean = active_loans_before == active_loans_after

        self.stdout.write("\n" + "━" * 62)
        self.stdout.write(
            self.style.SUCCESS(
                f"汇总：{passed}/{len(ALL_SCENARIOS) + 1} 项一致；"
                f"写路径回滚{'干净' if clean else '有残留！'}"
            )
        )
        if not clean:
            raise CommandError("写路径场景留下了数据残留，请检查回滚逻辑")
