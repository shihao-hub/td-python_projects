"""入口：uv run python -m apps.transactional_outbox [--scenario all|name] [--list]"""

from __future__ import annotations

import argparse
import asyncio
import socket

from sqlalchemy import text

from apps.common.lab import require_service
from apps.common.settings import settings
from apps.transactional_outbox.outbox import engine
from apps.transactional_outbox.scenarios import DESCRIPTIONS, SCENARIOS, run_all


async def _ensure_postgres() -> None:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        require_service("PostgreSQL", f"{exc.__class__.__name__}: {exc}")


def _ensure_kafka() -> None:
    host, port = settings.kafka_bootstrap.split(":")
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return
    except OSError as exc:
        require_service("Kafka", f"{exc.__class__.__name__}: {exc}")


async def main_async() -> None:
    parser = argparse.ArgumentParser(prog="transactional_outbox", description="事务性发件箱实验")
    parser.add_argument("--list", action="store_true", help="列出全部场景")
    parser.add_argument("--scenario", default="all", help="场景名或 all（all 含 Kafka 场景）")
    args = parser.parse_args()

    if args.list:
        print("transactional_outbox 场景清单：")
        for name, desc in DESCRIPTIONS.items():
            print(f"  {name:20s} {desc}")
        return

    await _ensure_postgres()
    needs_kafka = args.scenario in ("all", "kafka-e2e")
    if needs_kafka:
        _ensure_kafka()

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
