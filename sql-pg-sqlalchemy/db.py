"""数据库连接配置 —— 所有练习共用的入口。

Django 对照：
- Django 里这些信息写在 settings.py 的 DATABASES；
- SQLAlchemy 里叫 Engine（引擎），它 = 连接信息 + 连接池。
- Django 默认几乎每次请求都拿新连接；SQLAlchemy 的 Engine 内置连接池，
  连接用完归还池里复用，这也是 FastAPI 等 Web 框架的标准姿势。
"""
import os
import sys
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 防止 Windows 控制台中文乱码
for _stream in (sys.stdout, sys.stderr):
    if getattr(_stream, "encoding", "") and _stream.encoding.lower() != "utf-8":
        _stream.reconfigure(encoding="utf-8")

load_dotenv()  # 从项目根的 .env 读取配置（先复制 .env.example）

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")
PG_DBNAME = os.getenv("PG_DBNAME", "sql_pg_lab")

# echo=True：把 SQLAlchemy 发出的每条 SQL 打印到控制台，学习期强烈建议开启
ECHO_SQL = os.getenv("ECHO_SQL", "true").lower() != "false"


def build_url(dbname: str = PG_DBNAME) -> str:
    """拼连接串。psycopg 是 PostgreSQL 的 Python 驱动（v3，替代旧的 psycopg2）。"""
    return (
        f"postgresql+psycopg://{quote_plus(PG_USER)}:{quote_plus(PG_PASSWORD)}"
        f"@{PG_HOST}:{PG_PORT}/{dbname}"
    )


def make_engine(dbname: str = PG_DBNAME, echo: bool = ECHO_SQL):
    """按需创建引擎。Engine 是惰性的：创建时不连接，第一次执行 SQL 才真正连库。"""
    return create_engine(build_url(dbname), echo=echo, pool_pre_ping=True)


# 日常练习统一用这个引擎（整个进程一个，别到处建）
engine = make_engine()

# Session 工厂：Django 的 Model.objects 每次调用都隐式拿连接，
# SQLAlchemy 里则显式开一个 Session，用完关闭（with 上下文管理器）。
# expire_on_commit=False：commit 后对象属性不过期，避免访问属性时突然再发 SQL
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有模型的声明基类（对照 Django 的 models.Model）。"""


if not PG_PASSWORD:
    print("[提示] 未读到 PG_PASSWORD：请先复制 .env.example 为 .env 并填写 PostgreSQL 密码。")
