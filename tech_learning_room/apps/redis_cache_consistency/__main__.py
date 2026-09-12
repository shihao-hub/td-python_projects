"""入口：uv run python -m apps.redis_cache_consistency [--scenario all|name] [--list]"""

from __future__ import annotations

import argparse
import asyncio

import redis.asyncio as aioredis

from apps.common.lab import require_service
from apps.common.settings import settings
from apps.redis_cache_consistency.scenarios import DESCRIPTIONS, SCENARIOS, run_all


async def _ensure_redis() -> None:
    client = aioredis.from_url(settings.redis_url)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        require_service("Redis", f"{exc.__class__.__name__}: {exc}")
    finally:
        await client.aclose()


async def main_async() -> None:
    parser = argparse.ArgumentParser(prog="redis_cache_consistency", description="缓存一致性实验")
    parser.add_argument("--list", action="store_true", help="列出全部场景")
    parser.add_argument("--scenario", default="all", help="场景名或 all")
    args = parser.parse_args()

    if args.list:
        print("redis_cache_consistency 场景清单：")
        for name, desc in DESCRIPTIONS.items():
            print(f"  {name:20s} {desc}")
        return

    await _ensure_redis()
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
