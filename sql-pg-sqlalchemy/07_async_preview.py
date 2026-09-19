"""练习 7（选学）：异步 SQLAlchemy 一瞥 —— FastAPI 世界的主流姿势。

同步/异步对照：
    create_engine          →  create_async_engine
    sessionmaker           →  async_sessionmaker
    session.scalars(...)   →  (await session.scalars(...))
    with Session() as s    →  async with AsyncSession() as s

psycopg3 驱动同一个 URL 直接支持 asyncio，不用换驱动。
Django ORM 天生同步，所以这块对你是全新概念——看懂对照即可，不用背。
"""
import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db import PG_DBNAME, build_url
from models import Student


async def main() -> None:
    engine = create_async_engine(build_url(PG_DBNAME))
    AsyncSession = async_sessionmaker(engine, expire_on_commit=False)

    async with AsyncSession() as session:
        total = await session.scalar(select(func.count()).select_from(Student))
        latest = (
            (await session.scalars(select(Student).order_by(Student.id.desc()).limit(3))).all()
        )
        print(f"[1] 学生总数：{total}")
        print(f"[2] 最新 3 名：{latest}")

    await engine.dispose()  # 异步引擎关闭要 await


if __name__ == "__main__":
    asyncio.run(main())
