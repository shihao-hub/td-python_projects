"""实验 06 场景：merge → GGUF → Modelfile → ollama create → API 验收。

关键认知：
1. adapter 只是「补丁」，推理引擎不认识 peft 格式，必须先合并回基座；
2. 本机 Ollama(0.17.1) 的 safetensors 导入器不支持 Qwen3 架构，因此走
   llama.cpp 的 convert_hf_to_gguf.py 转 GGUF（f16），Ollama 从 GGUF 导入；
3. 4B 模型可在 ollama create 时加 --quantize q4_K_M 压进 8GB 显存；
4. 转换工具按需自动部署到 <数据根>/tools/llama.cpp（sparse checkout，约 2MB）。
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from apps.common import lab, models, paths, prompts

LLAMA_CPP_DIR = paths.DATA_ROOT / "tools" / "llama.cpp"
CONVERT_SCRIPT = LLAMA_CPP_DIR / "convert_hf_to_gguf.py"


def _resolve_name(model_alias: str, name: str | None) -> str:
    return name or f"policy-{model_alias.replace('/', '-').lower()}"


def _find_adapter(adapter_arg: str | None) -> Path | None:
    if adapter_arg:
        candidate = Path(adapter_arg)
        return candidate if candidate.exists() else None
    for prefix in ("lora", "qlora"):
        root = paths.OUTPUTS_DIR / prefix
        if not root.exists():
            continue
        candidates = [d for d in root.iterdir() if d.is_dir() and (d / "final").is_dir()]
        if candidates:
            return max(candidates, key=lambda d: d.stat().st_mtime) / "final"
    return None


def ensure_converter() -> Path:
    """转换器不存在时自动部署（sparse clone：conversion + gguf-py + requirements）。"""
    if CONVERT_SCRIPT.is_file():
        return CONVERT_SCRIPT
    LLAMA_CPP_DIR.parent.mkdir(parents=True, exist_ok=True)
    lab.step("首次运行：sparse clone llama.cpp 转换器（约 2MB）…")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
         "https://github.com/ggml-org/llama.cpp", str(LLAMA_CPP_DIR)],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        lab.warn(f"clone 失败: {result.stderr[-300:]}")
        raise SystemExit(3)
    subprocess.run(
        ["git", "-C", str(LLAMA_CPP_DIR), "sparse-checkout", "set", "conversion", "requirements", "gguf-py"],
        capture_output=True, timeout=300,
    )
    show = subprocess.run(
        ["git", "-C", str(LLAMA_CPP_DIR), "show", "HEAD:convert_hf_to_gguf.py"],
        capture_output=True, timeout=120,
    )
    CONVERT_SCRIPT.write_bytes(show.stdout)
    return CONVERT_SCRIPT


def scenario_merge(model_alias: str, adapter_arg: str | None, name: str) -> Path:
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    adapter = _find_adapter(adapter_arg)
    if adapter is None:
        lab.warn("找不到 adapter（先跑实验 03/04 训练，或 --adapter 指定路径）")
        raise SystemExit(2)

    model_ref = models.source(model_alias)
    out_dir = paths.OUTPUTS_DIR / "merged" / name
    lab.fact("基座", model_ref)
    lab.fact("adapter", adapter)
    lab.fact("输出", out_dir)

    lab.step("CPU 上加载基座并合并（不占显存，0.6B 约半分钟、4B 约 2 分钟）…")
    t0 = time.perf_counter()
    base = AutoModelForCausalLM.from_pretrained(model_ref, dtype="bfloat16", device_map="cpu")
    model = PeftModel.from_pretrained(base, str(adapter))
    merged = model.merge_and_unload()
    merged.save_pretrained(str(out_dir), safe_serialization=True)
    AutoTokenizer.from_pretrained(model_ref).save_pretrained(str(out_dir))
    lab.fact("合并耗时", f"{time.perf_counter() - t0:.0f}s")
    return out_dir


def scenario_gguf(merged_dir: Path, quant: str | None = None) -> Path:
    """HF safetensors → GGUF。quant 支持 f16 / q8_0（Python 转换器原生支持）。"""
    script = ensure_converter()
    out_gguf = merged_dir / (f"model-{quant}.gguf" if quant else "model-f16.gguf")
    outtype = quant or "f16"
    t0 = time.perf_counter()
    lab.step(f"转换 GGUF（outtype={outtype}，转换器走本地 gguf-py）…")
    result = subprocess.run(
        ["uv", "run", "--with", "sentencepiece", "python", str(script),
         str(merged_dir), "--outfile", str(out_gguf), "--outtype", outtype],
        capture_output=True, text=True, timeout=3600,
    )
    if result.returncode != 0 or not out_gguf.is_file():
        lab.warn(f"GGUF 转换失败: {result.stderr[-400:]}")
        raise SystemExit(3)
    lab.fact("GGUF", f"{out_gguf.name}（{out_gguf.stat().st_size / 1024**3:.2f} GB，耗时 {time.perf_counter() - t0:.0f}s）")
    return out_gguf


def scenario_modelfile(gguf_path: Path, name: str) -> Path:
    content = (
        f"FROM {gguf_path}\n\n"
        'SYSTEM """' + prompts.SYSTEM_PROMPT + '"""\n\n'
        "PARAMETER num_ctx 4096\n"
        "PARAMETER temperature 0.1\n"
        "PARAMETER repeat_penalty 1.05\n"
    )
    modelfile = gguf_path.parent / "Modelfile"
    modelfile.write_text(content, encoding="utf-8")
    lab.fact("Modelfile", modelfile)
    return modelfile


def scenario_create(modelfile: Path, name: str, quantize: str | None) -> None:
    cmd = ["ollama", "create", name, "-f", str(modelfile)]
    if quantize:
        cmd += ["--quantize", quantize]
    lab.step(f"{' '.join(cmd[:3])} …（导入 + 可选量化，1~3 分钟）")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        lab.warn(f"ollama create 失败: {(result.stderr or result.stdout)[-400:]}")
        raise SystemExit(3)
    lab.fact("已导入", f"ollama run {name}")


def scenario_chat(name: str, question: str) -> int:
    from apps.common import ollama_client

    t0 = time.perf_counter()
    answer = ollama_client.chat(
        name, [{"role": "user", "content": question}],
        temperature=0.1, num_ctx=4096, num_predict=512, timeout=300,
    )
    lab.fact("问题", question)
    lab.fact("回答", answer.strip()[:500])
    lab.fact("耗时", f"{time.perf_counter() - t0:.1f}s")
    lab.conclude(f"部署完成：ollama run {name} 可直接对话")
    return 0


def dispatch(scenario: str, model_alias: str, adapter_arg: str | None, name_arg: str | None,
             question: str, quantize: str | None) -> int:
    name = _resolve_name(model_alias, name_arg)
    merged_dir = paths.OUTPUTS_DIR / "merged" / name

    if scenario == "merge":
        lab.banner(f"实验 06-A 合并 adapter（{model_alias} → {name}）")
        scenario_merge(model_alias, adapter_arg, name)
        return 0
    if scenario == "gguf":
        lab.banner("实验 06-B 转换 GGUF")
        if not merged_dir.exists():
            lab.warn(f"先跑 merge：{merged_dir} 不存在")
            return 2
        scenario_gguf(merged_dir)
        return 0
    if scenario == "modelfile":
        lab.banner("实验 06-C 生成 Modelfile")
        gguf = merged_dir / "model-f16.gguf"
        if not gguf.exists():
            lab.warn("先跑 gguf 转换")
            return 2
        scenario_modelfile(gguf, name)
        return 0
    if scenario == "create":
        lab.banner(f"实验 06-D 导入 Ollama（{name}）")
        modelfile = merged_dir / "Modelfile"
        if not modelfile.exists():
            lab.warn("先跑 modelfile")
            return 2
        scenario_create(modelfile, name, quantize)
        return 0
    if scenario == "chat":
        lab.banner(f"实验 06-E 验收对话（{name}）")
        return scenario_chat(name, question)

    # deploy 一条龙：merge → gguf → modelfile → create → chat
    lab.banner(f"实验 06 部署一条龙（{model_alias} → ollama:{name}）")
    scenario_merge(model_alias, adapter_arg, name)
    scenario_gguf(merged_dir)
    scenario_modelfile(merged_dir / "model-f16.gguf", name)
    scenario_create(merged_dir / "Modelfile", name, quantize)
    return scenario_chat(name, question)
