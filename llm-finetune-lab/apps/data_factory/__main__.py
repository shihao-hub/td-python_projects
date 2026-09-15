"""实验 02 入口：uv run python -m apps.data_factory --scenario all。"""

from __future__ import annotations

import argparse

from apps.common import paths

paths.bootstrap()

from apps.data_factory import scenarios  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(prog="data_factory", description="制度文档 → SFT 数据集全流程")
    parser.add_argument(
        "--scenario",
        default="all",
        choices=["list", "stats", "extract", "chunk", "generate", "assemble", "split", "qc", "all"],
        help="all = extract + chunk + stats（generate 耗时长单独跑）",
    )
    parser.add_argument("--docx", default=None, help="指定 Word 文档路径（默认取 corpus/ 最新 .docx）")
    parser.add_argument("--model", default="qwen2.5:7b", help="QA 合成用的本地 Ollama 模型")
    parser.add_argument("--qa-per-chunk", type=int, default=2, help="每块生成的问答对数")
    parser.add_argument("--limit", type=int, default=None, help="generate 只处理前 N 个未完成块（试点用）")
    parser.add_argument("--no-uncovered", action="store_true", help="跳过边界问题生成")
    parser.add_argument("--ratios", default="0.8,0.1,0.1", help="train,val,test 比例")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--qc-n", type=int, default=15, help="每种类型抽样数")
    args = parser.parse_args()

    if args.scenario == "list":
        print("可用场景:")
        print("  extract   Word → 段落/标题/表格（01_extracted.jsonl）")
        print("  chunk     结构感知分块（02_chunks.jsonl）")
        print("  generate  本地 Ollama 合成 QA + 边界问题（03_qa_raw.jsonl，断点续跑）")
        print("  assemble  组装 SFT 样本：QA/背诵/拒答（04_sft_all.jsonl）")
        print("  split     按块分组切分 train/val/test（防泄漏）")
        print("  qc        抽样质检表（qc_samples.md）")
        print("  stats     各文件行数现状")
        print("  all       extract + chunk + stats")
        return

    ratios = tuple(float(x) for x in args.ratios.split(","))

    rc = 0
    if args.scenario in ("extract", "all"):
        rc |= scenarios.scenario_extract(args.docx)
    if args.scenario in ("chunk", "all"):
        rc |= scenarios.scenario_chunk()
    if args.scenario == "generate":
        rc |= scenarios.scenario_generate(
            args.model, args.qa_per_chunk, args.limit, not args.no_uncovered
        )
    if args.scenario == "assemble":
        rc |= scenarios.scenario_assemble()
    if args.scenario == "split":
        rc |= scenarios.scenario_split(ratios, args.seed)  # type: ignore[arg-type]
    if args.scenario == "qc":
        rc |= scenarios.scenario_qc(args.qc_n)
    if args.scenario in ("stats", "all"):
        rc |= scenarios.scenario_stats()

    raise SystemExit(rc)


if __name__ == "__main__":
    main()
