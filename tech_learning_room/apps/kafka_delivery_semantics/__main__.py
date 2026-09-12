"""入口：uv run python -m apps.kafka_delivery_semantics [--scenario all|name] [--list]"""

from __future__ import annotations

import argparse
import asyncio
import socket

from apps.common.lab import require_service
from apps.common.settings import settings
from apps.kafka_delivery_semantics.scenarios import DESCRIPTIONS, SCENARIOS, run_all


def _ensure_kafka() -> None:
    host, port = settings.kafka_bootstrap.split(":")
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return
    except OSError as exc:
        require_service("Kafka", f"{exc.__class__.__name__}: {exc}")


async def main_async() -> None:
    parser = argparse.ArgumentParser(prog="kafka_delivery_semantics", description="Kafka 投递语义实验")
    parser.add_argument("--list", action="store_true", help="列出全部场景")
    parser.add_argument("--scenario", default="all", help="场景名或 all")
    args = parser.parse_args()

    if args.list:
        print("kafka_delivery_semantics 场景清单：")
        for name, desc in DESCRIPTIONS.items():
            print(f"  {name:20s} {desc}")
        return

    _ensure_kafka()
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
