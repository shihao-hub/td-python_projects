"""入口：uv run python -m apps.sqlalchemy_session_lifecycle [--scenario all|name] [--list]"""

from __future__ import annotations

import argparse
import asyncio

from apps.common.lab import require_service
from apps.sqlalchemy_session_lifecycle.db import engine
from apps.sqlalchemy_session_lifecycle.scenarios import DESCRIPTIONS, SCENARIOS, run_all


async def _ensure_postgres() -> None:
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        require_service("PostgreSQL", f"{exc.__class__.__name__}: {exc}")


async def main_async() -> None:
    parser = argparse.ArgumentParser(prog="sqlalchemy_session_lifecycle", description="Session 生命周期实验")
    parser.add_argument("--list", action="store_true", help="列出全部场景")
    parser.add_argument("--scenario", default="all", help="场景名或 all")
    args = parser.parse_args()

    if args.list:
        print("sqlalchemy_session_lifecycle 场景清单：")
        for name, desc in DESCRIPTIONS.items():
            print(f"  {name:20s} {desc}")
        return

    await _ensure_postgres()
    try:
        if args.scenario == "all":
            await run_all()
        elif args.scenario in SCENARIOS:
            await SCENARIOS[args.scenario]()
        else:
            raise SystemExit(f"未知场景: {args.scenario}（--list 查看）")
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
