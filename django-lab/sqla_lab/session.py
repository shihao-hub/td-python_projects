"""engine / Session 工厂：与 Django 共用同一个数据库。

关键学习点 ——「共库」但**不共连接**：
- Django：自带连接池（CONN_MAX_AGE），事务由 ATOMIC_REQUESTS / atomic() 管理；
- SQLAlchemy：create_engine 自带连接池，Session 是独立的工作单元（Unit of Work）；
- 两个 ORM 各开各的连接、各管各的事务，看到的是同一份数据。
  Django 写完必须真正提交（autocommit 或 atomic 块结束），SQLAlchemy 才能看到；
  反之亦然。tests.py 的 TransactionVisibilityLessonTests 专门验证了这一点。

URL 不读静态配置而是从 django.db.connection 动态解析：Django 测试跑的
是 test_db.sqlite3（NAME 在建测试库后才切换），engine 跟着走才不会连错库 ——
这是「Django 管连接配置、SQLAlchemy 只管映射」的共库纪律。
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from django.db import connection
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session


def database_url(*, async_driver: bool = False) -> str:
    """把 Django 当前连接（settings_dict）翻译成 SQLAlchemy 的 dialect+driver URL。

    SQLAlchemy 用「dialect+driver」标记后端：
    - SQLite：同步内置、异步要 aiosqlite（SQLite 没有网络协议，驱动只是包了线程）；
    - PostgreSQL：Django 用 psycopg 3，SQLAlchemy 同步沿用 psycopg、异步换 asyncpg。
    """
    sd = connection.settings_dict
    if sd["ENGINE"] == "django.db.backends.sqlite3":
        name = str(sd["NAME"])
        if name.startswith("file:"):
            # Django 测试库是「共享内存库」（URI 形式）：透传并加 uri=true，
            # SQLAlchemy 才会以 SQLite URI 连进同一个内存库，而不是建同名文件
            url = f"sqlite:///{name}&uri=true"
        else:
            url = f"sqlite:///{Path(name).as_posix()}"
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1) if async_driver else url
    scheme = "postgresql+asyncpg" if async_driver else "postgresql+psycopg"
    auth = sd["USER"] or ""
    if sd["PASSWORD"]:
        auth += f":{sd['PASSWORD']}"
    host = sd["HOST"] or "localhost"
    if sd["PORT"]:
        host += f":{sd['PORT']}"
    return f"{scheme}://{auth}@{host}/{sd['NAME']}"


# 按 URL 缓存 engine：dev 库 / test 库各自一个连接池，互不串门
_engines: dict[str, Engine] = {}


def get_engine() -> Engine:
    """同步 engine（按当前数据库惰性单例）。runserver 多线程，SQLite 要放开同线程限制。"""
    url = database_url()
    if url not in _engines:
        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_pre_ping"] = True
        _engines[url] = create_engine(url, **kwargs)
    return _engines[url]


def dispose_engines() -> None:
    """关掉全部池连接（测试 teardown 用：否则 Windows 下测试库文件被句柄占着删不掉）。"""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()


def session_factory() -> Session:
    """对照 Django 的 `with transaction.atomic():` —— SQLAlchemy 用 Session 管事务。

    expire_on_commit=False：提交后对象不过期（Web 场景常用，避免提交后再访问属性触发重查）。
    """
    return Session(get_engine(), expire_on_commit=False)


def session_scope() -> Iterator[Session]:
    """「打开事务 → 提交 / 异常回滚」的惯用法，等价于 Django 的 atomic 上下文。"""
    with session_factory() as session, session.begin():
        yield session


@asynccontextmanager
async def async_session_scope() -> AsyncIterator[AsyncSession]:
    """AsyncSession 的标准用法：随用随建 engine、用完 dispose。

    为什么不缓存 async engine：aiosqlite 连接绑定创建它的事件循环，
    而 asyncio.run 每次都是新 loop，池里的旧连接在下一个 loop 里全是坏的 ——
    SQLite 下 engine 创建成本可忽略，这样最稳。对照 Django async ORM：
    它是「同步驱动 + 线程池」，压根没有 loop 归属问题（两种取舍都值得体会）。
    """
    engine = create_async_engine(database_url(async_driver=True))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()
