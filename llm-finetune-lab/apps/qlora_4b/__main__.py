"""实验 04 入口：uv run python -m apps.qlora_4b --scenario train|chat|list。"""

from __future__ import annotations

import argparse

from apps.common import paths

paths.bootstrap()

from apps.qlora_4b import scenarios  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(prog="qlora_4b", description="QLoRA：4bit 量化 + LoRA 微调大一点的模型")
    parser.add_argument("--scenario", default="list", choices=["list", "train", "chat"])
    parser.add_argument("--model", default="qwen3-4b", help="基座模型别名（默认 qwen3-4b）")
    parser.add_argument("--adapter", default=None, help="chat 场景：adapter 路径（默认取最新 qlora 输出）")
    parser.add_argument("--question", default="住宿费的单人单次上限是多少？", help="chat 场景的测试问题")
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--alpha", type=int, default=32)
    parser.add_argument("--max-length", type=int, default=768, help="8GB 显存的甜点：512/768/1024 依次更吃显存")
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--accum", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if args.scenario == "list":
        print("可用场景:")
        print("  train   4bit nf4 + LoRA 训练（paged 优化器 + 梯度检查点）")
        print("  chat    4bit 基座 + adapter 推理")
        return

    if args.scenario == "train":
        raise SystemExit(
            scenarios.scenario_train(
                model_alias=args.model,
                epochs=args.epochs,
                lr=args.lr,
                rank=args.rank,
                alpha=args.alpha,
                max_length=args.max_length,
                batch=args.batch,
                accum=args.accum,
                limit=args.limit,
                out_tag=args.out,
            )
        )
    raise SystemExit(scenarios.scenario_chat(args.model, args.adapter, args.question))


if __name__ == "__main__":
    main()
