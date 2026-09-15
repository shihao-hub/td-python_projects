# llm-finetune-lab

本地小模型微调学习实验室：以「让模型强约束于一份制度文档」为最终目标，手写 LoRA / QLoRA 全流程（transformers + peft + trl + bitsandbytes），所有训练数据生成与评测均使用本机 Ollama，文档不出本机。

## 快速开始

```powershell
uv sync                                            # 安装环境（torch 走 cu128 索引，支持 RTX 50 系）
uv run python main.py doctor                       # 环境自检（CUDA / bitsandbytes / Ollama / 数据目录）
uv run python main.py list                         # 实验目录

# 下载基座模型（国内推荐 ModelScope 源；HF 源可配 HF_ENDPOINT=https://hf-mirror.com）
uv run python main.py download qwen3-0.6b --source modelscope

# 运行实验（每个实验也可独立运行：uv run python -m apps.<app> --scenario xxx）
uv run python main.py run env_baseline --scenario all
```

## 实验目录（推荐学习顺序）

| # | 实验 | 主题 | 状态 |
|---|---|---|---|
| 01 | env_baseline | 环境冒烟 + 显存预算表（理论估算 + 实测） | 已实现 |
| 02 | data_factory | Word 制度文档 → 分块 → QA 合成（本地 Ollama）→ 切分 | 已实现 |
| 03 | lora_sft | 手写 LoRA 微调（Qwen3-0.6B / 1.7B） | 已实现 |
| 04 | qlora_4b | QLoRA 4bit 微调 4B（8GB 显存调优） | 已实现 |
| 05 | eval_lab | 评测：QA 正确率 / 越界拒答 / 幻觉率（本地裁判） | 已实现 |
| 06 | export_ollama | merge adapter → GGUF → Ollama 部署对话 | 已实现 |
| 07 | rag_compare | 基座+提示词 / RAG / 微调 / 混合 四方对照 | 已实现 |

## 约定

- 运行时数据（corpus / 模型缓存 / 数据集 / 输出 / 日志）一律放在 `%APPDATA%\language_projects\llm-finetune-lab\`，代码自动建目录；
- 制度文档只放本机 `corpus/`，AI 助手不读取其内容，只看统计数字；
- 依赖不可用时显式报错退出，不静默降级；
- 单元测试：`uv run pytest`（无需 GPU / Ollama）。

## 教程文档

中文教程见父仓库 `docs/python_projects/llm-finetune-lab/`。
