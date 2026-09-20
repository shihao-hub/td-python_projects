"""数据库连接配置 —— 所有练习共用的入口。

配置来源：liteconf 配置中心（默认 http://localhost:8646），
读取 /api/sql-pg-sqlalchemy/dev；服务未启动或配置不存在会直接报错退出。

liteconf 里支持的键（缺失时用默认值）：
    PG_HOST=localhost  PG_PORT=5432  PG_USER=postgres
    PG_PASSWORD（必填）  PG_DBNAME=sql_pg_lab  ECHO_SQL=true
临时覆盖：环境变量 LITECONF_URL 换服务地址；ECHO_SQL=false 关 SQL 打印。

Django 对照：
- Django 里这些信息写在 settings.py 的 DATABASES；
- SQLAlchemy 里叫 Engine（引擎），它 = 连接信息 + 连接池。
- Engine 内置连接池，连接用完归还复用——FastAPI 等 Web 框架的标准姿势。
"""
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 防止 Windows 控制台中文乱码
for _stream in (sys.stdout, sys.stderr):
    if getattr(_stream, "encoding", "") and _stream.encoding.lower() != "utf-8":
        _stream.reconfigure(encoding="utf-8")

LITECONF_URL = os.getenv("LITECONF_URL", "http://localhost:8646")
LITECONF_APP = "sql-pg-sqlalchemy"
LITECONF_ENV = "dev"

DEFAULTS = {
    "PG_HOST": "localhost",
    "PG_PORT": "5432",
    "PG_USER": "postgres",
    "PG_PASSWORD": "",
    "PG_DBNAME": "sql_pg_lab",
    "ECHO_SQL": "true",
}


def load_remote_config() -> dict[str, str]:
    """从 liteconf 拉配置（一次 GET）。服务不在 / app 未建 → 明确报错，绝不静默。"""
    url = f"{LITECONF_URL}/api/{LITECONF_APP}/{LITECONF_ENV}"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(
            f"[配置失败] liteconf 返回 HTTP {e.code}（{url}）："
            f"请确认 app={LITECONF_APP} env={LITECONF_ENV} 已创建"
            f"（Web 控制台 {LITECONF_URL}/ui/）"
        ) from e
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(
            f"[配置失败] 无法连接 liteconf（{url}）：{e}\n"
            f"请先启动配置服务：go_projects\\liteconf\\build\\liteconf-server.exe"
        ) from e
    if body.get("code") != "ok":
        raise SystemExit(f"[配置失败] liteconf 返回异常包络：{body}")
    content = body["data"]["content"]
    return {**DEFAULTS, **{k: v for k, v in content.items() if v is not None}}


CFG = load_remote_config()

PG_HOST = CFG["PG_HOST"]
PG_PORT = str(CFG["PG_PORT"])
PG_USER = CFG["PG_USER"]
PG_PASSWORD = CFG["PG_PASSWORD"]
PG_DBNAME = CFG["PG_DBNAME"]

# echo=True：把 SQLAlchemy 发出的每条 SQL 打印到控制台，学习期建议开启
ECHO_SQL = os.getenv("ECHO_SQL", CFG["ECHO_SQL"]).lower() != "false"

if not PG_PASSWORD:
    raise SystemExit("[配置失败] liteconf 配置里缺少 PG_PASSWORD，请在 Web 控制台补上该键")


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
