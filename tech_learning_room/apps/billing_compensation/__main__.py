"""入口：uv run python -m apps.billing_compensation [--scenario all|name] [--list]"""

from __future__ import annotations

import argparse

from apps.billing_compensation.db import connect
from apps.billing_compensation.scenarios import DESCRIPTIONS, SCENARIOS, run_all
from apps.common.lab import require_service


def _ensure_postgres() -> None:
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        require_service("PostgreSQL", f"{exc.__class__.__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="billing_compensation", description="计费与补偿实验")
    parser.add_argument("--list", action="store_true", help="列出全部场景")
    parser.add_argument("--scenario", default="all", help="场景名或 all")
    args = parser.parse_args()

    if args.list:
        print("billing_compensation 场景清单：")
        for name, desc in DESCRIPTIONS.items():
            print(f"  {name:22s} {desc}")
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
