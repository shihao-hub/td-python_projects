"""实验 06 入口：uv run python -m apps.export_ollama --scenario deploy|merge|modelfile|create|chat|list。"""

from __future__ import annotations

import argparse

from apps.common import paths

paths.bootstrap()

from apps.export_ollama import scenarios  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(prog="export_ollama", description="merge adapter → Ollama 部署")
    parser.add_argument("--scenario", default="list",
                        choices=["list", "merge", "gguf", "modelfile", "create", "chat", "deploy"])
    parser.add_argument("--model", default="qwen3-0.6b")
    parser.add_argument("--adapter", default=None, help="adapter 路径（默认取最新 lora/qlora 输出）")
    parser.add_argument("--name", default=None, help="Ollama 模型名（默认 policy-<模型别名>）")
    parser.add_argument("--quantize", default=None, choices=["q4_K_M", "q5_K_M", "q8_0"],
                        help="ollama create 时顺带量化（4B 建议 q4_K_M）")
    parser.add_argument("--question", default="迟到超过三十分钟怎么处理？", help="chat 验收问题")
    args = parser.parse_args()

    if args.scenario == "list":
        print("可用场景:")
        print("  merge      adapter 合并进基座（CPU，不占显存）")
        print("  gguf       HF → GGUF 转换（转换器自动部署到数据目录 tools/）")
        print("  modelfile  生成 Modelfile（SYSTEM 固化提示词）")
        print("  create     ollama create 导入（可 --quantize q4_K_M）")
        print("  chat       Ollama API 验收对话")
        print("  deploy     上述全部一条龙")
        return

    raise SystemExit(
        scenarios.dispatch(args.scenario, args.model, args.adapter, args.name,
                           args.question, args.quantize)
    )


if __name__ == "__main__":
    main()
