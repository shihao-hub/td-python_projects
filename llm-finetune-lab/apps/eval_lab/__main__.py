"""实验 05 入口：uv run python -m apps.eval_lab --scenario run|compare|list。"""

from __future__ import annotations

import argparse

from apps.common import paths

paths.bootstrap()

from apps.eval_lab import scenarios  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(prog="eval_lab", description="微调前后对比评测（本地裁判）")
    parser.add_argument("--scenario", default="list", choices=["list", "run", "compare"])
    parser.add_argument("--tag", default=None, help="run 场景的报告名（默认 eval_<模型>_<配置>）")
    parser.add_argument("--model", default="qwen3-0.6b")
    parser.add_argument("--adapter", default=None, help="被测 adapter 路径")
    parser.add_argument("--no-system", action="store_true", help="不加 system prompt（测裸基座）")
    parser.add_argument("--load", default="bf16", choices=["bf16", "4bit"], help="被测模型加载方式")
    parser.add_argument("--sample", type=int, default=100, help="从 test 集抽样 N 条（None 全量）")
    parser.add_argument("--judge", default="qwen2.5:7b", help="裁判模型（本地 Ollama）")
    args = parser.parse_args()

    if args.scenario == "list":
        print("可用场景:")
        print("  run      单配置评测（默认基座+system prompt；--adapter 切换微调模型）")
        print("  compare  base-sys vs adapter 对比 + 汇总表")
        return

    use_system = not args.no_system
    if args.scenario == "run":
        tag = args.tag or f"eval_{args.model.replace('/', '_')}{'_adapter' if args.adapter else '_base'}"
        raise SystemExit(
            scenarios.scenario_run(tag, args.model, args.adapter, use_system, args.load,
                                   args.sample, args.judge, 256)
        )
    raise SystemExit(scenarios.scenario_compare(args.model, args.adapter, args.load, args.sample, args.judge))


if __name__ == "__main__":
    main()
