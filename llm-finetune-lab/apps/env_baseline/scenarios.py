"""实验 01 场景实现：环境信息 / 显存预算 / 真实加载实测 / 算力基准。

学习目标：
1. 确认本机是 Blackwell（sm_120）+ cu128 的可用训练环境；
2. 建立「显存预算」心智模型：权重 / 优化器 / 激活 / KV Cache 各占多少；
3. 用理论估算表对照真实加载实测，理解 8GB 显存的能力边界。
"""

from __future__ import annotations

import importlib.metadata as md
import platform
import time

from apps.common import lab, models, paths, vram

# 各训练方式的经验系数（bs=1、梯度检查点开启、seq=1024 档位）
# 只用于教学估算，真实数值以 measure 实测为准
TRAIN_PEAK_HINT_GB = {
    "qwen3-0.6b": {"lora_bf16": 4.0, "qlora_4bit": 2.5},
    "qwen3-1.7b": {"lora_bf16": 7.0, "qlora_4bit": 4.0},
    "qwen3-4b": {"lora_bf16": None, "qlora_4bit": 6.0},
}


def scenario_info() -> int:
    lab.banner("实验 01-A 环境与版本信息")

    lab.step(f"操作系统: {platform.system()} {platform.release()} ({platform.version()})")
    lab.step(f"Python:   {platform.python_version()}  ({paths.PROJECT_ROOT})")

    for name in ("torch", "transformers", "peft", "trl", "datasets", "accelerate", "bitsandbytes"):
        try:
            lab.fact(name, md.version(name))
        except md.PackageNotFoundError:
            lab.warn(f"{name} 未安装")

    lab.step("")
    info = vram.device_info()
    if not info.get("available"):
        lab.warn("CUDA 不可用，后续实验无法进行（本实验室不支持 CPU 降级）")
        return 1
    lab.fact("GPU", info["device_name"])
    lab.fact("算力等级", f"sm_{str(info['capability']).replace('.', '')}（{info['capability']}）")
    lab.fact("显存总量", vram.human_bytes(info["total_memory"]))
    lab.fact("SM 数量", info["multi_processor_count"])
    lab.fact("CUDA 运行时", info["cuda_runtime"])

    lab.step("")
    lab.fact("数据根目录", paths.DATA_ROOT)
    lab.fact("磁盘空间", paths.free_space_text())
    lab.fact("HF_HOME", paths.HF_CACHE_DIR)
    lab.fact("教程文档", "父仓库 docs/projects/python_projects/llm-finetune-lab/")

    lab.conclude("环境信息正常。下一步：--scenario budget 建立显存预算心智模型")
    return 0


def scenario_budget() -> int:
    lab.banner("实验 01-B 显存预算表（理论估算）")

    lab.step("显存占用公式：")
    lab.step("  推理 = 权重 + KV Cache + 框架开销(约 0.5~1GB)")
    lab.step("  训练 = 权重 + 适配器/梯度 + 优化器状态 + 激活(梯度检查点可大幅压缩) + 框架开销")
    lab.step("")

    header = f"  {'模型':<12} {'bf16 权重':>10} {'4bit 权重':>10} {'LoRA-bf16 峰值':>16} {'QLoRA 峰值':>12} {'8GB 结论':<14}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for alias, params in models.PARAM_COUNTS.items():
        w_bf16 = params * 2
        w_4bit = params * 0.55  # nf4 权重 + 量化常数开销
        hints = TRAIN_PEAK_HINT_GB[alias]
        lora = hints["lora_bf16"]
        qlora = hints["qlora_4bit"]

        if alias == "qwen3-4b":
            verdict = "仅 QLoRA 可行"
        elif alias == "qwen3-1.7b":
            verdict = "LoRA 可行(紧)"
        else:
            verdict = "LoRA 宽裕"
        print(
            f"  {alias:<12} {vram.human_bytes(w_bf16):>10} {vram.human_bytes(w_4bit):>10} "
            f"{(f'{lora:.1f} GB' if lora else '不可行'):>16} {qlora:.1f} GB{'':>6} {verdict:<14}"
        )

    lab.step("")
    lab.step("读数提示：")
    lab.step("  - 4bit 权重 = 参数数 × 约 0.55 字节（nf4 + 每块量化常数），约为 bf16 的 1/4；")
    lab.step("  - LoRA-bf16 峰值含冻结权重、LoRA 梯度/优化器、激活；QLoRA 峰值已考虑 4bit 权重 + 反量化瞬时开销；")
    lab.step("  - 上表为经验估算，真实数值用 --scenario measure 实测验证；")
    lab.step("  - 8GB 卡上 4B 的 bf16 LoRA 训练不可行（权重 8GB 已占满），必须 4bit 量化 —— 这就是 QLoRA 的意义。")

    lab.conclude("这是后续选择「哪个模型 + 哪种微调方式」的决策依据")
    return 0


def scenario_measure(model_alias: str, mode: str, max_new_tokens: int) -> int:
    lab.banner(f"实验 01-C 真实加载实测（{model_alias} / {mode}）")
    lab.require_gpu()

    try:
        model_ref = models.source(model_alias)  # 本地目录优先，否则 repo id
    except KeyError as exc:
        print(f"  [NG] {exc}")
        return 2

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    modes = ["bf16", "4bit"] if mode == "both" else [mode]
    failures = 0
    for m in modes:
        failures |= _measure_one(model_ref, m, max_new_tokens)

    lab.conclude("实测完成：把这张表与实验 01-B 的估算对照，理解差异来源")
    return failures


def _measure_one(repo: str, mode: str, max_new_tokens: int) -> int:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"\n  --- {mode} 加载 {repo} ---")
    vram.reset_peak()
    t0 = time.perf_counter()

    try:
        tokenizer = AutoTokenizer.from_pretrained(repo)
        if mode == "bf16":
            model = AutoModelForCausalLM.from_pretrained(
                repo,
                dtype=torch.bfloat16,
                device_map={"": "cuda:0"},
            )
        else:
            from transformers import BitsAndBytesConfig

            quant_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
            )
            model = AutoModelForCausalLM.from_pretrained(
                repo,
                quantization_config=quant_config,
                device_map={"": "cuda:0"},
            )
        model.eval()
    except Exception as exc:  # noqa: BLE001
        lab.warn(f"{mode} 加载失败: {exc!r}")
        lab.warn("若为下载问题：uv run python main.py download " + repo)
        return 1

    load_s = time.perf_counter() - t0
    lab.fact("加载耗时", f"{load_s:.1f}s")
    lab.fact("加载后 allocated", vram.human_bytes(vram.allocated()))
    lab.fact("加载后 reserved", vram.human_bytes(vram.reserved()))
    lab.fact("加载峰值", vram.human_bytes(vram.peak()))

    # 生成一小段，观察激活/KV Cache 的增量
    free_before, _ = vram.free_total()
    messages = [{"role": "user", "content": "用一句话说明什么是大语言模型。"}]
    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    inputs = tokenizer(text, return_tensors="pt").to("cuda")
    vram.reset_peak()
    t1 = time.perf_counter()
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    gen_s = time.perf_counter() - t1
    new_tokens = output.shape[1] - inputs["input_ids"].shape[1]
    reply = tokenizer.decode(output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True)

    lab.fact("生成", f"{new_tokens} tokens / {gen_s:.1f}s（{new_tokens / max(gen_s, 1e-9):.1f} tok/s）")
    lab.fact("生成峰值增量", vram.human_bytes(vram.peak()))
    lab.fact("对话预览", reply.strip().replace("\n", " ")[:80] + "...")

    del model
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    return 0


def scenario_bench() -> int:
    lab.banner("实验 01-D bf16 矩阵乘算力基准（Tensor Core）")
    lab.require_gpu()

    import torch

    size = 4096
    iters = 30
    for dtype, label in ((torch.bfloat16, "bf16"), (torch.float16, "fp16"), (torch.float32, "fp32(tf32关闭)")):
        x = torch.randn(size, size, device="cuda", dtype=dtype)
        y = torch.randn(size, size, device="cuda", dtype=dtype)
        for _ in range(5):  # 预热
            _ = x @ y
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        for _ in range(iters):
            _ = x @ y
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - t0
        tflops = 2 * size**3 * iters / elapsed / 1e12
        lab.fact(label, f"{tflops:.1f} TFLOPS")
        del x, y
        torch.cuda.empty_cache()

    lab.step("")
    lab.step("用途：用这个数字粗估训练时间。例：LoRA 反向+优化器开销约为前向 3 倍，")
    lab.step("有效算力按 1/3 计，可估出「每个 epoch 需要多久」，再决定 batch/seq 怎么取舍。")

    lab.conclude("为实验 03/04 的「时间预算」提供基准")
    return 0
