"""入口：uv run python -m apps.celery_task_reliability [--scenario all|name] [--list]"""

from __future__ import annotations

import argparse

from apps.celery_task_reliability.db import connect, reset_schema
from apps.celery_task_reliability.scenarios import DESCRIPTIONS, SCENARIOS, run_all
from apps.common.lab import require_service
from apps.common.settings import settings


def _ensure_postgres() -> None:
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        require_service("PostgreSQL", f"{exc.__class__.__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="celery_task_reliability", description="任务可靠性实验")
    parser.add_argument("--list", action="store_true", help="列出全部场景")
    parser.add_argument("--scenario", default="all", help="场景名或 all")
    parser.add_argument("--reset", action="store_true", help="运行前重置 schema")
    args = parser.parse_args()

    if args.list:
        print("celery_task_reliability 场景清单：")
        for name, desc in DESCRIPTIONS.items():
            print(f"  {name:22s} {desc}")
        print(f"\n启动真实 worker（retry-backoff 场景需要，Windows 必须 solo）：\n"
              f"  uv run celery -A apps.celery_task_reliability.celery_lab worker --pool=solo -l info")
        print(f"broker/backend: {settings.redis_url}")
        return

    _ensure_postgres()
    if args.reset:
        reset_schema()
    if args.scenario == "all":
        run_all()
    elif args.scenario in SCENARIOS:
        SCENARIOS[args.scenario]()
    else:
        raise SystemExit(f"未知场景: {args.scenario}（--list 查看）")


if __name__ == "__main__":
    main()
