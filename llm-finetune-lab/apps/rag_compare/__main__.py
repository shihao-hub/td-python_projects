"""实验 07 入口：uv run python -m apps.rag_compare --scenario prepare|index|ask|compare|list。"""

from __future__ import annotations

import argparse

from apps.common import paths

paths.bootstrap()

from apps.rag_compare import scenarios  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(prog="rag_compare", description="基座/检索/微调/混合 四方对照")
    parser.add_argument("--scenario", default="list", choices=["list", "prepare", "index", "ask", "compare"])
    parser.add_argument("--embed-model", default="bge-m3", help="Ollama 向量模型")
    parser.add_argument("--base-model", default="qwen2.5:7b", help="基座（Ollama 名）")
    parser.add_argument("--ft-model", default="policy-qwen3-0.6b", help="微调模型（实验 06 部署名）")
    parser.add_argument("--judge", default="qwen2.5:7b")
    parser.add_argument("--question", default="住宿费的单人单次上限是多少？")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--sample", type=int, default=50, help="compare 抽样题数")
    args = parser.parse_args()

    if args.scenario == "list":
        print("可用场景:")
        print("  prepare   拉取向量模型等依赖（bge-m3 约 1.2GB）")
        print("  index     全量块向量化，建检索索引")
        print("  ask       单问四方对照（base / rag / ft / ft-rag）")
        print("  compare   test 集抽样批量对照 + 汇总表")
        return

    if args.scenario == "prepare":
        raise SystemExit(scenarios.scenario_prepare(args.embed_model, args.judge))
    if args.scenario == "index":
        raise SystemExit(scenarios.scenario_index(args.embed_model))
    if args.scenario == "ask":
        raise SystemExit(scenarios.scenario_ask(args.question, args.embed_model, args.base_model, args.ft_model, args.top_k))
    raise SystemExit(
        scenarios.scenario_compare(args.sample, args.embed_model, args.base_model, args.ft_model,
                                   args.top_k, args.judge)
    )


if __name__ == "__main__":
    main()
