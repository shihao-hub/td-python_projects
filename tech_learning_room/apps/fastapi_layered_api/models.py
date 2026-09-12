"""ORM 模型与引擎：collections（收藏夹）+ collection_items（条目）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from apps.common.settings import settings

SCHEMA = "tlr_api"

engine = create_async_engine(settings.database_url, pool_size=5)
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class CollectionRow(Base):
    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("name"), {"schema": SCHEMA})

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CollectionItemRow(Base):
    __tablename__ = "collection_items"
    __table_args__ = (UniqueConstraint("collection_id", "seq"), {"schema": SCHEMA})

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.collections.id"))
    seq: Mapped[int] = mapped_column()
    title: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def reset_schema() -> None:
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        await conn.run_sync(Base.metadata.create_all)
