"""实验 01 入口：uv run python -m apps.env_baseline --scenario info|budget|measure|bench|all。"""

from __future__ import annotations

import argparse
import sys

# 任何 HF 相关导入之前先 bootstrap
from apps.common import paths

paths.bootstrap()

from apps.env_baseline import scenarios  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="env_baseline",
        description="环境冒烟与显存预算：理论表 + 实测",
    )
    parser.add_argument(
        "--scenario",
        default="all",
        choices=["list", "info", "budget", "measure", "bench", "all"],
        help="all = info + budget + bench（measure 需先下载模型）",
    )
    parser.add_argument("--model", default="qwen3-0.6b", help="measure 场景的模型别名")
    parser.add_argument("--mode", default="both", choices=["bf16", "4bit", "both"], help="measure 场景的加载方式")
    parser.add_argument("--max-new-tokens", type=int, default=64, help="measure 场景的生成长度")
    args = parser.parse_args()

    if args.scenario == "list":
        print("可用场景:")
        print("  info     环境与版本信息")
        print("  budget   显存预算表（理论估算，瞬间完成）")
        print("  bench    bf16 矩阵乘算力基准（约 10 秒）")
        print("  measure  真实加载模型实测显存（需先下载模型）")
        print("  all      info + budget + bench")
        return

    rc = 0
    if args.scenario in ("info", "all"):
        rc |= scenarios.scenario_info()
    if args.scenario in ("budget", "all"):
        rc |= scenarios.scenario_budget()
    if args.scenario == "measure":
        rc |= scenarios.scenario_measure(args.model, args.mode, args.max_new_tokens)
    if args.scenario in ("bench", "all"):
        rc |= scenarios.scenario_bench()

    raise SystemExit(rc)


if __name__ == "__main__":
    main()
