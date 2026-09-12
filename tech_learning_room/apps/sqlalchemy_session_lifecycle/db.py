"""引擎、模型与会话工厂。

关键参数：pool_size=2 / max_overflow=0 / pool_timeout=2 —— 刻意配小，
让'长事务占满连接池'的故障在一台笔记本上 2 秒内复现。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from apps.common.settings import settings

SCHEMA = "tlr_slc"

# 连接池刻意调小：2 个常驻连接、不允许溢出、等连接最多 2 秒
engine = create_async_engine(
    settings.database_url,
    pool_size=2,
    max_overflow=0,
    pool_timeout=2.0,
)

Session = async_sessionmaker(engine, expire_on_commit=False)


def pool_status() -> str:
    """连接池当前状态：size=已建立 checkedin=空闲 inuse=被占用。"""
    return engine.sync_engine.pool.status()


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    result_text: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditRow(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def reset_schema() -> None:
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        await conn.run_sync(Base.metadata.create_all)
        for i in range(1, 6):
            await conn.execute(
                text(f"INSERT INTO {SCHEMA}.tasks (name, status) VALUES ('task-{i}', 'pending')")
            )
