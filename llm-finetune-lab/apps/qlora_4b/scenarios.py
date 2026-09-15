"""实验 04 场景：QLoRA 训练与推理。

与实验 03（LoRA）的关键差异，正是 QLoRA 的四个组成：
1. nf4 量化加载（BitsAndBytesConfig：4bit NormalFloat + 双重量化）；
2. prepare_model_for_kbit_training：给量化模型做梯度检查点前的数值/梯度修正；
3. paged_adamw_8bit 优化器：优化器状态用统一内存分页，防瞬时尖峰 OOM；
4. 序列长度成为最敏感的显存旋钮（512 → 768 → 1024 逐级试探）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from apps.common import lab, models, paths, prompts, vram


def _load_messages(path: Path, limit: int | None = None) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if limit is not None:
        rows = rows[:limit]
    return [{"messages": row["messages"]} for row in rows]


def _quant_config():
    import torch
    from transformers import BitsAndBytesConfig

    return BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",           # 4bit NormalFloat：信息论上最优的正态分布量化格
        bnb_4bit_use_double_quant=True,      # 量化常数本身再量化，每参数再省约 0.4 bit
        bnb_4bit_compute_dtype=torch.bfloat16,
    )


def _latest_adapter(prefix: str) -> Path | None:
    root = paths.OUTPUTS_DIR / prefix
    if not root.exists():
        return None
    candidates = [d for d in root.iterdir() if d.is_dir() and (d / "final").is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime) / "final"


def scenario_train(
    model_alias: str,
    epochs: float,
    lr: float,
    rank: int,
    alpha: int,
    max_length: int,
    batch: int,
    accum: int,
    limit: int | None,
    out_tag: str | None,
) -> int:
    lab.banner(f"实验 04-A QLoRA 训练（{model_alias}，nf4 4bit）")
    lab.require_gpu()

    train_path = paths.DATASETS_DIR / "train.jsonl"
    val_path = paths.DATASETS_DIR / "val.jsonl"
    if not train_path.exists() or not val_path.exists():
        lab.warn(f"缺少数据集：{train_path} / {val_path}")
        lab.warn("先运行数据工厂生成 train/val.jsonl")
        return 2

    train_rows = _load_messages(train_path, limit)
    val_rows = _load_messages(val_path)
    lab.fact("训练样本", f"{len(train_rows)} 条（limit={limit}）")
    lab.fact("验证样本", f"{len(val_rows)} 条")

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    model_ref = models.source(model_alias)
    lab.fact("基座模型", model_ref)
    lab.step(f"超参: epochs={epochs} lr={lr} r={rank} alpha={alpha} max_len={max_length} bs={batch}x{accum}")

    tokenizer = AutoTokenizer.from_pretrained(model_ref)
    model = AutoModelForCausalLM.from_pretrained(
        model_ref,
        quantization_config=_quant_config(),
        device_map={"": "cuda:0"},
    )
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True,
                                            gradient_checkpointing_kwargs={"use_reentrant": False})
    model.config.use_cache = False

    lora_config = LoraConfig(
        r=rank,
        lora_alpha=alpha,
        lora_dropout=0.05,
        target_modules="all-linear",
        task_type="CAUSAL_LM",
        bias="none",
    )
    model = get_peft_model(model, lora_config)
    trainable, total = model.get_nb_trainable_parameters()
    lab.fact("可训练参数", f"{trainable:,} / {total:,}（{trainable / total * 100:.2f}%）")
    lab.fact("量化后权重占用", vram.human_bytes(vram.allocated()))

    timestamp = time.strftime("%m%d_%H%M%S")
    out_dir = paths.OUTPUTS_DIR / "qlora" / (out_tag or f"{model_alias.replace('/', '_')}_{timestamp}")
    config = SFTConfig(
        output_dir=str(out_dir),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch,
        gradient_accumulation_steps=accum,
        learning_rate=lr,
        lr_scheduler_type="cosine",
        warmup_steps=0.05,
        weight_decay=0.01,
        max_grad_norm=1.0,
        bf16=True,
        max_length=max_length,
        assistant_only_loss=True,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        optim="paged_adamw_8bit",  # QLoRA 标配：分页 8bit 优化器，防显存尖峰
        logging_steps=5,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=3,
        report_to="none",
        seed=42,
    )

    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=Dataset.from_list(train_rows),
        eval_dataset=Dataset.from_list(val_rows) if val_rows else None,
        processing_class=tokenizer,
    )

    lab.step("")
    lab.step("开始训练（4bit 反量化在算子内进行：bf16 精度计算、4bit 存储）…")
    vram.reset_peak()
    t0 = time.perf_counter()
    try:
        trainer.train()
    except torch.cuda.OutOfMemoryError:
        lab.warn("OOM：按序尝试 --max-length 768→512、--accum 加大、关闭其他占显存进程")
        return 3
    elapsed = time.perf_counter() - t0

    final_dir = out_dir / "final"
    trainer.save_model(str(final_dir))
    tokenizer.save_pretrained(str(final_dir))

    eval_losses = [h["eval_loss"] for h in trainer.state.log_history if "eval_loss" in h]
    step_losses = [h["loss"] for h in trainer.state.log_history if "loss" in h]
    summary_loss = next((h["train_loss"] for h in reversed(trainer.state.log_history) if "train_loss" in h), None)
    first_loss = step_losses[0] if step_losses else summary_loss
    last_loss = step_losses[-1] if step_losses else summary_loss

    report = {
        "method": "qlora-nf4",
        "model": model_alias,
        "model_ref": model_ref,
        "adapter": str(final_dir),
        "train_samples": len(train_rows),
        "val_samples": len(val_rows),
        "hyperparams": {
            "epochs": epochs, "lr": lr, "r": rank, "alpha": alpha,
            "max_length": max_length, "batch": batch, "accum": accum,
            "optim": "paged_adamw_8bit",
        },
        "trainable_params": trainable,
        "total_params": total,
        "train_seconds": round(elapsed, 1),
        "vram_peak": vram.human_bytes(vram.peak()),
        "train_loss_first": first_loss,
        "train_loss_last": last_loss,
        "eval_losses": eval_losses,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (out_dir / "train_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lab.fact("训练耗时", f"{elapsed / 60:.1f} 分钟")
    lab.fact("显存峰值", report["vram_peak"])
    lab.fact("train loss", f"{first_loss} → {last_loss}")
    lab.fact("eval loss", " → ".join(f"{x:.4f}" for x in eval_losses) if eval_losses else "（无验证集）")
    lab.fact("adapter", final_dir)

    if len(eval_losses) >= 2 and eval_losses[-1] > eval_losses[0]:
        lab.warn("eval loss 末段高于首段：可能过拟合，考虑减 epoch 或加数据")
    lab.conclude("QLoRA 训练完成。评测走实验 05，部署走实验 06")
    return 0


def scenario_chat(model_alias: str, adapter_arg: str | None, question: str) -> int:
    lab.banner(f"实验 04-B QLoRA 模型推理（{model_alias}）")
    lab.require_gpu()

    adapter = Path(adapter_arg) if adapter_arg else _latest_adapter("qlora")
    if adapter is None or not adapter.exists():
        lab.warn(f"找不到 adapter：{adapter}（先跑 --scenario train，或用 --adapter 指定路径）")
        return 2

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_ref = models.source(model_alias)
    lab.fact("基座", model_ref)
    lab.fact("adapter", adapter)

    tokenizer = AutoTokenizer.from_pretrained(model_ref)
    base = AutoModelForCausalLM.from_pretrained(
        model_ref, quantization_config=_quant_config(), device_map={"": "cuda:0"}
    )
    model = PeftModel.from_pretrained(base, str(adapter))
    model.eval()

    messages = [
        {"role": "system", "content": prompts.SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    inputs = tokenizer(text, return_tensors="pt").to("cuda")
    t0 = time.perf_counter()
    with torch.no_grad():
        output = model.generate(**inputs, max_new_tokens=256, do_sample=False,
                                pad_token_id=tokenizer.eos_token_id)
    elapsed = time.perf_counter() - t0
    new_tokens = output.shape[1] - inputs["input_ids"].shape[1]
    reply = tokenizer.decode(output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)

    lab.fact("问题", question)
    lab.fact("回答", reply.strip())
    lab.fact("速度", f"{new_tokens} tokens / {elapsed:.1f}s（4bit 反量化比 bf16 慢属正常）")
    lab.fact("显存峰值", vram.human_bytes(vram.peak()))
    lab.conclude("4bit 推理质量略低于合并后 bf16，正式部署用实验 06 的 merge 流程")
    return 0
