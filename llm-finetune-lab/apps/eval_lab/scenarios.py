"""实验 05 场景：微调前后对比评测。

流程两阶段（重要：被测模型与 Ollama 裁判共用一块 8GB 显卡，必须分时复用）：
  阶段 1 生成：被测模型把 test 集全部答完 → 释放显存；
  阶段 2 判分：Ollama 裁判逐条打分 → 汇总指标。
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from apps.common import lab, models, paths, prompts
from apps.eval_lab import judge


def _load_test(sample: int | None, seed: int = 123) -> list[dict]:
    test_path = paths.DATASETS_DIR / "test.jsonl"
    if not test_path.exists():
        return []
    rows = []
    with test_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if sample is not None and sample < len(rows):
        rows = random.Random(seed).sample(rows, sample)
    return rows


def _generate_answers(samples: list[dict], model_alias: str, adapter: Path | None,
                      use_system: bool, load_mode: str, max_new_tokens: int) -> list[dict]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_ref = models.source(model_alias)
    tokenizer = AutoTokenizer.from_pretrained(model_ref)

    kwargs = {"dtype": torch.bfloat16, "device_map": {"": "cuda:0"}}
    if load_mode == "4bit":
        from apps.qlora_4b.scenarios import _quant_config

        kwargs = {"quantization_config": _quant_config(), "device_map": {"": "cuda:0"}}
    model = AutoModelForCausalLM.from_pretrained(model_ref, **kwargs)

    if adapter is not None:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, str(adapter))
    model.eval()

    results: list[dict] = []
    t0 = time.perf_counter()
    for i, sample_row in enumerate(samples, 1):
        question = next(m["content"] for m in sample_row["messages"] if m["role"] == "user")
        messages = ([{"role": "system", "content": prompts.SYSTEM_PROMPT}] if use_system else []) + [
            {"role": "user", "content": question}
        ]
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        inputs = tokenizer(text, return_tensors="pt").to("cuda")
        with torch.no_grad():
            output = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                                    pad_token_id=tokenizer.eos_token_id)
        answer = tokenizer.decode(output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        results.append({**sample_row, "model_answer": answer.strip()})
        if i % 20 == 0 or i == len(samples):
            elapsed = time.perf_counter() - t0
            print(f"  生成进度 [{i}/{len(samples)}] {elapsed / i:.1f}s/条 剩余约 {(elapsed / i * (len(samples) - i)) / 60:.0f} 分钟")

    del model
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    return results


def _judge_all(answered: list[dict], judge_model: str) -> list[dict]:
    details = []
    t0 = time.perf_counter()
    for i, row in enumerate(answered, 1):
        verdict = judge.verdict_for_sample(row, row["model_answer"], judge_model)
        question = next(m["content"] for m in row["messages"] if m["role"] == "user")
        reference = next(m["content"] for m in row["messages"] if m["role"] == "assistant")
        details.append({
            "type": row["type"],
            "question": question,
            "reference": reference,
            "model_answer": row["model_answer"],
            **verdict,
        })
        if i % 20 == 0 or i == len(answered):
            elapsed = time.perf_counter() - t0
            print(f"  判分进度 [{i}/{len(answered)}] {elapsed / i:.1f}s/条")
    return details


def _write_report(out_dir: Path, tag: str, meta: dict, details: list[dict]) -> dict:
    metrics = judge.aggregate(details)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"tag": tag, **meta, "metrics": metrics,
              "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    (out_dir / "eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [f"# 评测明细（{tag}）", "", f"指标：{json.dumps(metrics, ensure_ascii=False)}", ""]
    for d in details:
        lines.append(f"## [{d['verdict']}] {d['type']} | {d['question']}")
        lines.append(f"- 参考: {d['reference'][:120]}")
        lines.append(f"- 模型: {d['model_answer'][:200]}")
        lines.append(f"- 理由: {d['reason']}")
        lines.append("")
    (out_dir / "eval_details.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def scenario_run(tag: str, model_alias: str, adapter_arg: str | None, use_system: bool,
                 load_mode: str, sample: int | None, judge_model: str, max_new_tokens: int) -> int:
    lab.banner(f"实验 05 评测（{tag}）")

    samples = _load_test(sample)
    if not samples:
        lab.warn(f"没有测试集：{paths.DATASETS_DIR / 'test.jsonl'}（先跑数据工厂 split）")
        return 2

    by_type: dict[str, int] = {}
    for s in samples:
        by_type[s["type"]] = by_type.get(s["type"], 0) + 1
    lab.fact("测试样本", f"{len(samples)} 条 {by_type}")
    lab.fact("被测模型", f"{model_alias}（{'带' if use_system else '无'}system prompt，adapter={adapter_arg or '无'}）")
    lab.fact("裁判", judge_model)

    # 裁判在线检查（一次真实调用，失败即退出）
    try:
        judge.judge_one("测试", "测试答案", "测试答案", judge_model)
    except Exception as exc:  # noqa: BLE001
        lab.warn(f"裁判不可用: {exc}")
        lab.warn(f"先启动 Ollama 并确认模型存在: ollama pull {judge_model}")
        return 3

    adapter = Path(adapter_arg) if adapter_arg else None
    lab.step("")
    lab.step("阶段 1/2：被测模型生成答案…")
    answered = _generate_answers(samples, model_alias, adapter, use_system, load_mode, max_new_tokens)

    lab.step("阶段 2/2：Ollama 裁判判分…")
    details = _judge_all(answered, judge_model)

    out_dir = paths.OUTPUTS_DIR / "eval" / tag
    report = _write_report(out_dir, tag, {"model": model_alias, "adapter": str(adapter), "use_system": use_system}, details)

    m = report["metrics"]
    lab.fact("QA 准确率", f"{m['qa_accuracy']}（严格 {m['qa_strict_accuracy']}，{m['qa_total']} 题）")
    lab.fact("拒答正确率", f"{m['refusal_correct']}（{m['refusal_total']} 题）")
    lab.fact("幻觉率", m["hallucination_rate"])
    lab.fact("该答不答率", m["qa_overrefusal_rate"])
    lab.fact("报告", out_dir)
    lab.conclude("与基座/其他配置的对比跑 compare 场景，或直接看多个 tag 的报告")
    return 0


def scenario_compare(model_alias: str, adapter_arg: str | None, load_mode: str,
                     sample: int | None, judge_model: str) -> int:
    lab.banner("实验 05 对比评测：base-sys（基座+提示词） vs adapter（微调）")

    base_tag = f"base-sys-{model_alias}"
    rc1 = scenario_run(base_tag, model_alias, None, True, load_mode, sample, judge_model, 256)
    rc2 = 0
    if adapter_arg:
        rc2 = scenario_run(f"adapter-{model_alias}", model_alias, adapter_arg, True, load_mode, sample, judge_model, 256)
    elif (paths.OUTPUTS_DIR / "lora").exists() or (paths.OUTPUTS_DIR / "qlora").exists():
        lab.warn("未指定 --adapter；如需对比微调效果，请传 --adapter <路径>")

    if rc1 or rc2:
        return rc1 or rc2

    # 汇总对比表
    lab.banner("对比结果")
    rows = []
    for tag in (base_tag, f"adapter-{model_alias}"):
        report_path = paths.OUTPUTS_DIR / "eval" / tag / "eval_report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            m = report["metrics"]
            rows.append((tag, m["qa_accuracy"], m["refusal_correct"], m["hallucination_rate"]))
    print(f"  {'配置':<28} {'QA准确率':>10} {'拒答正确率':>10} {'幻觉率':>8}")
    for tag, qa, ref, hall in rows:
        print(f"  {tag:<28} {str(qa):>10} {str(ref):>10} {str(hall):>8}")

    lab.conclude("微调的价值 = 拒答正确率与幻觉率的改善；QA 准确率差距则主要靠 RAG（实验 07）")
    return 0
