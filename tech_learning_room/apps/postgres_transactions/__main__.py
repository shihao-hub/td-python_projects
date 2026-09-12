"""入口：uv run python -m apps.postgres_transactions [--scenario all|name] [--list]"""

from __future__ import annotations

import argparse

import psycopg

from apps.common.lab import require_service
from apps.postgres_transactions.db import connect
from apps.postgres_transactions.scenarios import SCENARIOS, describe, run_all


def _ensure_postgres() -> None:
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        require_service("PostgreSQL", f"{exc.__class__.__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="postgres_transactions", description="并发扣减与事务隔离实验")
    parser.add_argument("--list", action="store_true", help="列出全部场景")
    parser.add_argument("--scenario", default="all", help="场景名或 all")
    args = parser.parse_args()

    if args.list:
        print("postgres_transactions 场景清单：")
        for name, fn in SCENARIOS.items():
            if name.startswith("__desc_"):
                continue
            print(f"  {name:22s} {describe(name)}")
        return

    _ensure_postgres()
    if args.scenario == "all":
        run_all()
    elif args.scenario in SCENARIOS:
        SCENARIOS[args.scenario]()
    else:
        raise SystemExit(f"未知场景: {args.scenario}（--list 查看）")


if __name__ == "__main__":
    main()
