# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# setup.py —— 一键初始化学习库 learn_pg（可重复执行 = 完全重置）
# 建库 → 建表 → 灌 100 万订单 + 200 万明细，预计 1~2 分钟
#
# 用法（在 setup 目录或项目内执行均可）：
#   uv run setup/setup.py                          # 默认 postgres@localhost:5432
#   uv run setup/setup.py --db-user postgres       # 指定用户
#
# 密码读取顺序：环境变量 PGPASSWORD > %APPDATA%\postgresql\pgpass.conf > 启动时提示输入

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

SETUP_DIR = Path(__file__).resolve().parent

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def locate_psql() -> str:
    """定位 psql：PATH 优先，退回 Program Files 下最高版本的 PostgreSQL。"""
    psql = shutil.which("psql")
    if psql:
        return psql
    pg_root = Path("C:/Program Files/PostgreSQL")
    if pg_root.is_dir():
        candidates = sorted(pg_root.glob("*/bin/psql.exe"), reverse=True)
        if candidates:
            return str(candidates[0])
    raise FileNotFoundError("找不到 psql。请确认已安装 PostgreSQL，并把 <安装目录>\\bin 加入 PATH")


def prepare_password(db_user: str) -> None:
    """按 PGPASSWORD > pgpass.conf > 交互提示 的顺序准备密码（写入环境变量传给子进程）。"""
    pgpass = Path(os.environ.get("APPDATA", "")) / "postgresql" / "pgpass.conf"
    if not os.environ.get("PGPASSWORD") and not pgpass.is_file():
        os.environ["PGPASSWORD"] = getpass.getpass(f"请输入 PostgreSQL 用户 {db_user} 的密码：")


def run_sql(psql: str, file: str, db: str, db_host: str, port: int, db_user: str) -> None:
    print(f"\n>>> 执行 {file} （库: {db}）")
    r = subprocess.run(
        [psql, "-h", db_host, "-p", str(port), "-U", db_user, "-d", db,
         "-v", "ON_ERROR_STOP=1", "-f", str(SETUP_DIR / file)],
    )
    if r.returncode != 0:
        raise RuntimeError(f"{file} 执行失败（退出码 {r.returncode}）")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="一键初始化学习库 learn_pg：建库 → 建表 → 灌 100 万订单 + 200 万明细"
    )
    parser.add_argument("--db-host", default="localhost", help="数据库主机（默认 localhost）")
    parser.add_argument("--port", type=int, default=5432, help="端口（默认 5432）")
    parser.add_argument("--db-user", default="postgres", help="用户名（默认 postgres）")
    parser.add_argument("--db-name", default="learn_pg", help="库名（默认 learn_pg）")
    args = parser.parse_args()

    # psql 客户端编码统一 UTF-8，避免中文乱码（py 下无需 chcp）
    os.environ["PGCLIENTENCODING"] = "UTF8"

    try:
        psql = locate_psql()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"使用 psql: {psql}")

    prepare_password(args.db_user)

    try:
        # 三步 SQL 依次执行
        run_sql(psql, "01_create_db.sql", "postgres", args.db_host, args.port, args.db_user)  # 建库（连接默认库执行）
        run_sql(psql, "02_schema.sql", args.db_name, args.db_host, args.port, args.db_user)   # 建表
        run_sql(psql, "03_seed.sql", args.db_name, args.db_host, args.port, args.db_user)     # 灌数据

        # 验收：各表行数（orders 应约 100 万）
        print("\n>>> 验收：各表行数（orders 应约 100 万）")
        r = subprocess.run(
            [psql, "-h", args.db_host, "-p", str(args.port), "-U", args.db_user, "-d", args.db_name,
             "-v", "ON_ERROR_STOP=1", "-c",
             "SELECT 'users' AS tbl, count(*) FROM users UNION ALL SELECT 'products', count(*) FROM products "
             "UNION ALL SELECT 'orders', count(*) FROM orders UNION ALL SELECT 'order_items', count(*) FROM order_items;"],
        )
        if r.returncode != 0:
            raise RuntimeError("验收查询失败")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("\n完成！日常连接方式：")
    print(f"  psql -h {args.db_host} -p {args.port} -U {args.db_user} -d {args.db_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
