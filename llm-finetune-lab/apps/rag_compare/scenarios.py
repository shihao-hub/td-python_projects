"""实验 07 场景：prepare / index / ask / compare。

四方对照（全部走 Ollama，与训练互不抢显存）：
  base     基座 + system prompt（无文档知识）
  rag      基座 + system + 检索片段
  ft       微调模型（实验 06 部署的 policy-*）
  ft-rag   微调模型 + 检索片段（混合方案，通常是最终答案）
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from apps.common import lab, ollama_client, paths, prompts
from apps.eval_lab import judge
from apps.rag_compare import rag

INDEX_PATH = paths.DATASETS_DIR / rag.INDEX_PATH_NAME
CHUNKS_PATH = paths.DATASETS_DIR / "02_chunks.jsonl"


def _ensure_ready(embed_model: str, judge_model: str) -> None:
    names = [m["name"] for m in ollama_client.list_models()]
    for model in (embed_model, judge_model):
        if not any(n == model or n.startswith(model + ":") for n in names):
            raise SystemExit(f"!! Ollama 缺少模型 {model}，执行: ollama pull {model}")


def _load_all(embed_model: str) -> tuple[list[dict], np.ndarray | None]:
    chunks = rag.load_chunks(CHUNKS_PATH)
    if not chunks:
        raise SystemExit("!! 没有 02_chunks.jsonl，先跑数据工厂 extract+chunk")
    matrix = None
    if INDEX_PATH.exists():
        matrix, _ = rag.load_index(INDEX_PATH)
        if matrix.shape[0] != len(chunks):
            lab.warn("块数与索引不一致，需要重建：--scenario index")
            matrix = None
    return chunks, matrix


def scenario_prepare(embed_model: str, judge_model: str) -> int:
    lab.banner("实验 07-A 准备模型")
    import subprocess

    names = [m["name"] for m in ollama_client.list_models()]
    if not any(n == embed_model or n.startswith(embed_model + ":") for n in names):
        lab.step(f"拉取向量模型 {embed_model}（约 1.2GB）…")
        rc = subprocess.run(["ollama", "pull", embed_model], timeout=1800).returncode
        if rc != 0:
            lab.warn("拉取失败，检查网络后重试")
            return 3
    _ensure_ready(embed_model, judge_model)
    lab.fact("向量模型", embed_model)
    lab.fact("裁判模型", judge_model)
    lab.conclude("就绪。下一步：--scenario index 建索引")
    return 0


def scenario_index(embed_model: str) -> int:
    lab.banner(f"实验 07-B 建立向量索引（{embed_model}）")
    _ensure_ready(embed_model, "qwen2.5:7b")

    chunks = rag.load_chunks(CHUNKS_PATH)
    if not chunks:
        lab.warn("没有 02_chunks.jsonl，先跑数据工厂")
        return 2
    t0 = time.perf_counter()
    rag.build_index(chunks, embed_model, INDEX_PATH)
    lab.fact("索引", f"{INDEX_PATH}（{len(chunks)} 块，{time.perf_counter() - t0:.0f}s）")
    lab.conclude("下一步：--scenario ask 单问体验 或 --scenario compare 批量对照")
    return 0


def scenario_ask(question: str, embed_model: str, base_model: str, ft_model: str, top_k: int) -> int:
    lab.banner(f"实验 07-C 单问四方对照（top-{top_k}）")
    _ensure_ready(embed_model, "qwen2.5:7b")

    chunks, matrix = _load_all(embed_model)
    if matrix is None:
        lab.warn("索引缺失，先跑 --scenario index")
        return 2

    hits = rag.retrieve(question, embed_model, chunks, matrix, top_k)
    lab.fact("检索命中", " / ".join(f"{h['section_path']}({h['score']:.2f})" for h in hits))

    paths_cfg = [
        ("base", [{"role": "system", "content": prompts.SYSTEM_PROMPT},
                  {"role": "user", "content": question}], base_model),
        ("rag", rag.compose_rag_messages(question, hits, prompts.SYSTEM_PROMPT), base_model),
    ]
    names = [m["name"] for m in ollama_client.list_models()]
    if any(n == ft_model or n.startswith(ft_model + ":") for n in names):
        paths_cfg.append(("ft", [{"role": "user", "content": question}], ft_model))
        paths_cfg.append(("ft-rag", rag.compose_rag_messages(question, hits, prompts.SYSTEM_PROMPT), ft_model))
    else:
        lab.warn(f"微调模型 {ft_model} 未部署（实验 06），跳过 ft / ft-rag")

    lab.fact("问题", question)
    for tag, messages, model in paths_cfg:
        t0 = time.perf_counter()
        answer = ollama_client.chat(model, messages, temperature=0.1, num_ctx=8192,
                                    num_predict=512, timeout=600)
        print(f"\n  [{tag}]（{model}，{time.perf_counter() - t0:.1f}s）")
        print("   " + answer.strip()[:400].replace("\n", "\n   "))

    lab.conclude("对比关注：rag 是否答对细节 / ft 是否拒答 / ft-rag 是否兼具两者")
    return 0


def scenario_compare(sample: int, embed_model: str, base_model: str, ft_model: str,
                     top_k: int, judge_model: str) -> int:
    lab.banner("实验 07-D 批量四方对照（test 集 QA 题抽样）")
    _ensure_ready(embed_model, judge_model)

    chunks, matrix = _load_all(embed_model)
    if matrix is None:
        lab.warn("索引缺失，先跑 --scenario index")
        return 2

    test_path = paths.DATASETS_DIR / "test.jsonl"
    if not test_path.exists():
        lab.warn("没有 test.jsonl，先跑数据工厂 split")
        return 2
    qa_rows = []
    with test_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line.strip())
            if row.get("type") in ("qa", "quote"):
                qa_rows.append(row)
    if not qa_rows:
        lab.warn("test 集没有 qa/quote 样本")
        return 2
    rows = random.Random(9).sample(qa_rows, min(sample, len(qa_rows)))
    lab.fact("抽样", f"{len(rows)}/{len(qa_rows)} 题")

    names = [m["name"] for m in ollama_client.list_models()]
    ft_ready = any(n == ft_model or n.startswith(ft_model + ":") for n in names)
    paths_cfg = [
        ("base", lambda q, h: [{"role": "system", "content": prompts.SYSTEM_PROMPT},
                               {"role": "user", "content": q}], base_model),
        ("rag", lambda q, h: rag.compose_rag_messages(q, h, prompts.SYSTEM_PROMPT), base_model),
    ]
    if ft_ready:
        paths_cfg.append(("ft", lambda q, h: [{"role": "user", "content": q}], ft_model))
        paths_cfg.append(("ft-rag", lambda q, h: rag.compose_rag_messages(q, h, prompts.SYSTEM_PROMPT), ft_model))

    from apps.eval_lab.judge import judge_one

    results: dict[str, list[dict]] = {tag: [] for tag, _, _ in paths_cfg}
    for i, row in enumerate(rows, 1):
        question = next(m["content"] for m in row["messages"] if m["role"] == "user")
        reference = next(m["content"] for m in row["messages"] if m["role"] == "assistant")
        hits = rag.retrieve(question, embed_model, chunks, matrix, top_k)
        for tag, build_messages, model in paths_cfg:
            answer = ollama_client.chat(model, build_messages(question, hits),
                                        temperature=0.1, num_ctx=8192, num_predict=512, timeout=600)
            verdict = judge_one(question, reference, answer, judge_model)
            results[tag].append({"question": question, "reference": reference,
                                 "answer": answer, **verdict})
        if i % 5 == 0 or i == len(rows):
            print(f"  进度 [{i}/{len(rows)}]")

    lab.banner("对照结果")
    out_dir = paths.OUTPUTS_DIR / "rag_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    print(f"  {'方案':<10} {'准确率(correct+partial)':>22} {'答错率':>8} {'拒答率':>8}")
    for tag, details in results.items():
        n = len(details)
        correct = sum(1 for d in details if d["verdict"] in ("correct", "partial"))
        wrong = sum(1 for d in details if d["verdict"] == "wrong")
        refused = sum(1 for d in details if d["verdict"] == "refused")
        summary[tag] = {"n": n, "correct": correct / n, "wrong": wrong / n, "refused": refused / n}
        print(f"  {tag:<10} {correct / n:>22.2%} {wrong / n:>8.2%} {refused / n:>8.2%}")
    (out_dir / "compare_report.json").write_text(
        json.dumps({"summary": summary, "details": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lab.fact("明细报告", out_dir / "compare_report.json")
    lab.conclude("最终方案建议：ft-rag（微调管行为 + 检索管事实）通常是「牢记制度文档」的最优解")
    return 0
