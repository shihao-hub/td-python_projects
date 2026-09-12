"""入口：uv run python -m apps.python_asyncio_concurrency [--scenario all|name] [--list]"""

from __future__ import annotations

import argparse
import asyncio

from apps.python_asyncio_concurrency.scenarios import DESCRIPTIONS, SCENARIOS, run_all


def main() -> None:
    parser = argparse.ArgumentParser(prog="python_asyncio_concurrency", description="异步并发实验（纯标准库）")
    parser.add_argument("--list", action="store_true", help="列出全部场景")
    parser.add_argument("--scenario", default="all", help="场景名或 all")
    args = parser.parse_args()

    if args.list:
        print("python_asyncio_concurrency 场景清单（无外部依赖）：")
        for name, desc in DESCRIPTIONS.items():
            print(f"  {name:22s} {desc}")
        return

    if args.scenario == "all":
        asyncio.run(run_all())
    elif args.scenario in SCENARIOS:
        asyncio.run(SCENARIOS[args.scenario]())
    else:
        raise SystemExit(f"未知场景: {args.scenario}（--list 查看）")


if __name__ == "__main__":
    main()
