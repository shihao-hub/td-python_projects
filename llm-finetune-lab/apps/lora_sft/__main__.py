"""实验 03 入口：uv run python -m apps.lora_sft --scenario train|chat|list。"""

from __future__ import annotations

import argparse

from apps.common import paths

paths.bootstrap()

from apps.lora_sft import scenarios  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(prog="lora_sft", description="手写 LoRA 微调全闭环")
    parser.add_argument("--scenario", default="list", choices=["list", "train", "chat"])
    parser.add_argument("--model", default="qwen3-0.6b", help="基座模型别名或 repo id")
    parser.add_argument("--adapter", default=None, help="chat 场景：LoRA adapter 路径（默认取最新训练输出）")
    parser.add_argument("--question", default="迟到超过三十分钟怎么处理？", help="chat 场景的测试问题")
    # 训练超参（默认值是 8GB 显存下的甜点配置）
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--alpha", type=int, default=32)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--batch", type=int, default=1, help="per-device batch size")
    parser.add_argument("--accum", type=int, default=8, help="梯度累积步数")
    parser.add_argument("--limit", type=int, default=None, help="只用前 N 条训练（冒烟测试）")
    parser.add_argument("--out", default=None, help="输出目录名（默认 lora_<模型>_<时间戳>）")
    args = parser.parse_args()

    if args.scenario == "list":
        print("可用场景:")
        print("  train   加载 train/val.jsonl → LoRA 训练 → 保存 adapter + 训练报告")
        print("  chat    加载基座 + adapter，单问推理对比（--question 指定问题）")
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
