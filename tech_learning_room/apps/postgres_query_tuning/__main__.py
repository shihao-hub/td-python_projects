"""入口：uv run python -m apps.postgres_query_tuning [--scenario all|name] [--list] [--keep-data]"""

from __future__ import annotations

import argparse
import asyncio

from apps.common.lab import require_service, step
from apps.postgres_query_tuning.db import connect, reset_and_seed
from apps.postgres_query_tuning.scenarios import DESCRIPTIONS, SCENARIOS, run_all


async def _ensure_postgres() -> None:
    try:
        conn = await connect()
        await conn.execute("SELECT 1")
        await conn.close()
    except Exception as exc:  # noqa: BLE001
        require_service("PostgreSQL", f"{exc.__class__.__name__}: {exc}")


async def main_async() -> None:
    parser = argparse.ArgumentParser(prog="postgres_query_tuning", description="查询调优实验")
    parser.add_argument("--list", action="store_true", help="列出全部场景")
    parser.add_argument("--scenario", default="all", help="场景名或 all")
    parser.add_argument("--keep-data", action="store_true", help="跳过重新造数（复用上次数据）")
    args = parser.parse_args()

    if args.list:
        print("postgres_query_tuning 场景清单：")
        for name, desc in DESCRIPTIONS.items():
            print(f"  {name:20s} {desc}")
        return

    await _ensure_postgres()
    if not args.keep_data:
        step("初始化：建表 + 造数（1 万用户 / 10 万订单 / 20 万日志，约数秒）...")
        await reset_and_seed()
    if args.scenario == "all":
        await run_all()
    elif args.scenario in SCENARIOS:
        await SCENARIOS[args.scenario]()
    else:
        raise SystemExit(f"未知场景: {args.scenario}（--list 查看）")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
