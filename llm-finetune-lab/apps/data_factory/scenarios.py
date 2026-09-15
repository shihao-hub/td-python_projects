"""实验 02 场景编排：extract → chunk → generate → assemble → split → qc/stats。"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from apps.common import lab, paths, prompts
from apps.data_factory import assemble as assemble_mod
from apps.data_factory import chunker, docx_extract, generate, splitter

EXTRACTED_PATH = paths.DATASETS_DIR / "01_extracted.jsonl"
CHUNKS_PATH = paths.DATASETS_DIR / "02_chunks.jsonl"
QA_RAW_PATH = paths.DATASETS_DIR / "03_qa_raw.jsonl"
SFT_ALL_PATH = paths.DATASETS_DIR / "04_sft_all.jsonl"
SPLIT_PATHS = {
    "train": paths.DATASETS_DIR / "train.jsonl",
    "val": paths.DATASETS_DIR / "val.jsonl",
    "test": paths.DATASETS_DIR / "test.jsonl",
}
MANIFEST_PATH = paths.DATASETS_DIR / "split_manifest.json"
QC_PATH = paths.DATASETS_DIR / "qc_samples.md"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def scenario_extract(docx_arg: str | None) -> int:
    lab.banner("实验 02-A Word 提取")

    docx_path = Path(docx_arg) if docx_arg else docx_extract.find_corpus_docx(paths.CORPUS_DIR)
    if docx_path is None or not docx_path.exists():
        lab.warn(f"corpus 目录没有 .docx 文件：{paths.CORPUS_DIR}")
        lab.warn("把制度文档（Word）放进去后重跑，或用 --docx 指定路径")
        return 2

    lab.fact("输入文件", f"{docx_path}（{docx_path.stat().st_size / 1024:.0f} KB）")
    t0 = time.perf_counter()
    blocks = docx_extract.extract_docx(docx_path)
    lab.fact("提取耗时", f"{time.perf_counter() - t0:.1f}s")

    headings = [b for b in blocks if b.kind == "heading"]
    paras = [b for b in blocks if b.kind == "para"]
    tables = [b for b in blocks if b.kind == "table"]
    total_chars = sum(len(b.text) for b in blocks)
    _write_jsonl(EXTRACTED_PATH, [b.to_dict() for b in blocks])

    lab.fact("标题", f"{len(headings)} 个")
    lab.fact("段落", f"{len(paras)} 个")
    lab.fact("表格", f"{len(tables)} 个")
    lab.fact("总字符", f"{total_chars:,}")
    lab.fact("输出", EXTRACTED_PATH)

    if headings:
        level1 = [h.text for h in headings if h.level == 1]
        preview = "、".join(level1[:8]) + ("……" if len(level1) > 8 else "")
        lab.fact("一级章节预览", preview or "（无一级标题，检查 Word 样式）")
    else:
        lab.warn("未识别到任何标题！分块将退化为纯段落模式（质量下降），建议给文档章节套用「标题」样式")

    lab.conclude("提取完成。下一步：--scenario chunk")
    return 0


def scenario_chunk() -> int:
    lab.banner("实验 02-B 结构感知分块")

    blocks = [docx_extract.Block(b["kind"], b["text"], b.get("level")) for b in _read_jsonl(EXTRACTED_PATH)]
    if not blocks:
        lab.warn(f"没有提取结果，先运行 --scenario extract（{EXTRACTED_PATH}）")
        return 2

    chunks = chunker.chunk_blocks(blocks)
    _write_jsonl(CHUNKS_PATH, [c.to_dict() for c in chunks])

    lengths = [len(c.text) for c in chunks]
    avg = sum(lengths) / max(len(lengths), 1)
    lab.fact("块数", len(chunks))
    lab.fact("平均块长", f"{avg:.0f} 字（最长 {max(lengths)} / 最短 {min(lengths)}）")
    lab.fact("全量生成预估", f"约 {len(chunks) * 40 / 3600:.1f} 小时（按 40s/块估算，支持断点续跑）")
    lab.fact("输出", CHUNKS_PATH)

    lab.step("")
    lab.step("试点建议：先用 --scenario generate --limit 20 跑 20 块，抽检质量后再全量")

    lab.conclude("分块完成。下一步：--scenario generate（需要 Ollama 在线）")
    return 0


def scenario_generate(model: str, qa_per_chunk: int, limit: int | None, with_uncovered: bool) -> int:
    lab.banner(f"实验 02-C QA 合成（模型 {model}）")

    chunks = _read_jsonl(CHUNKS_PATH)
    if not chunks:
        lab.warn("没有分块结果，先运行 --scenario chunk")
        return 2

    try:
        stats = generate.generate_qa(
            chunks, QA_RAW_PATH, model,
            qa_per_chunk=qa_per_chunk, limit=limit, with_uncovered=with_uncovered,
        )
    except SystemExit as exc:
        lab.warn(str(exc))
        return 3

    lab.fact("已完成块数", f"{stats['done_before'] + stats['generated']}/{stats['total']}")
    lab.fact("本次生成", stats["generated"])
    lab.fact("错误块数", f"{stats['errors']}（重跑本场景即可重试）")
    lab.fact("输出", QA_RAW_PATH)

    lab.conclude("生成可随时中断续跑。下一步：--scenario assemble")
    return 0


def scenario_assemble() -> int:
    lab.banner("实验 02-D 组装 SFT 数据集")

    chunks = _read_jsonl(CHUNKS_PATH)
    if not chunks:
        lab.warn("没有分块结果，先运行 --scenario chunk")
        return 2
    if not QA_RAW_PATH.exists():
        lab.warn("没有 QA 生成结果，先运行 --scenario generate（本次仅组装背诵+拒答样本）")

    result = assemble_mod.assemble(QA_RAW_PATH if QA_RAW_PATH.exists() else None, chunks)
    samples = result["samples"]
    _write_jsonl(SFT_ALL_PATH, samples)

    lab.fact("QA 样本", result["by_type"].get("qa", 0))
    lab.fact("条文背诵样本", result["by_type"].get("quote", 0))
    lab.fact("拒答样本", result["by_type"].get("refusal", 0))
    lab.fact("去重丢弃", result["dropped"])
    lab.fact("合计", f"{len(samples)} 条")
    lab.fact("输出", SFT_ALL_PATH)

    lab.conclude("组装完成。下一步：--scenario split")
    return 0


def scenario_split(ratios: tuple[float, float, float], seed: int) -> int:
    lab.banner("实验 02-E 按块分组切分")

    samples = _read_jsonl(SFT_ALL_PATH)
    if not samples:
        lab.warn("没有 SFT 样本，先运行 --scenario assemble")
        return 2

    train, val, test, manifest = splitter.split_grouped(samples, ratios, seed)
    for name, split in SPLIT_PATHS.items():
        rows = {"train": train, "val": val, "test": test}[name]
        _write_jsonl(split, rows)
    manifest["fingerprints"] = {
        name: splitter.group_fingerprint([s["id"] for s in split])
        for name, split in (("train", train), ("val", val), ("test", test))
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    for name in ("train", "val", "test"):
        info = manifest[name]
        lab.fact(name, f"{info['samples']} 条 {info['by_type']}")
    lab.fact("切分清单", MANIFEST_PATH)

    # 泄漏自检：同一 chunk 不允许跨 split
    chunk_sets = {}
    for name, split in (("train", train), ("val", val), ("test", test)):
        chunk_sets[name] = {s["chunk_id"] for s in split if s.get("chunk_id")}
    overlap = (chunk_sets["train"] & chunk_sets["val"]) | (chunk_sets["train"] & chunk_sets["test"]) | (chunk_sets["val"] & chunk_sets["test"])
    lab.check(not overlap, "无跨 split 的 chunk 泄漏", f"发现泄漏 chunk: {sorted(overlap)[:5]}")

    lab.conclude("数据集就绪：datasets/train.jsonl / val.jsonl / test.jsonl")
    return 0


def scenario_qc(n: int) -> int:
    lab.banner("实验 02-F 抽样质检")

    samples = _read_jsonl(SFT_ALL_PATH)
    if not samples:
        lab.warn("没有 SFT 样本，先运行 --scenario assemble")
        return 2

    rng = random.Random(7)
    lines = ["# 数据质检抽样表", "", "逐条检查：问题是否自然 / 答案是否忠于原文 / 拒答是否合理。", ""]
    for sample_type in ("qa", "quote", "refusal"):
        pool = [s for s in samples if s["type"] == sample_type]
        lines.append(f"## {sample_type}（抽 {min(n, len(pool))}/{len(pool)}）")
        lines.append("")
        for s in rng.sample(pool, min(n, len(pool))):
            q = next(m["content"] for m in s["messages"] if m["role"] == "user")
            a = next(m["content"] for m in s["messages"] if m["role"] == "assistant")
            lines.append(f"- **Q**: {q}")
            lines.append(f"  **A**: {a}")
            lines.append(f"  （章节: {s.get('chunk_id') or '-'}）")
        lines.append("")

    QC_PATH.write_text("\n".join(lines), encoding="utf-8")
    lab.fact("输出", QC_PATH)
    lab.conclude("人工审阅 qc_samples.md，不合格率高就调 QA_GEN_TEMPLATE 重生成")
    return 0


def scenario_stats() -> int:
    lab.banner("实验 02 数据集现状")

    for name, path in (
        ("01 提取结果", EXTRACTED_PATH),
        ("02 分块", CHUNKS_PATH),
        ("03 QA 原始", QA_RAW_PATH),
        ("04 SFT 全量", SFT_ALL_PATH),
        ("train", SPLIT_PATHS["train"]),
        ("val", SPLIT_PATHS["val"]),
        ("test", SPLIT_PATHS["test"]),
    ):
        if path.exists():
            count = sum(1 for _ in path.open("r", encoding="utf-8"))
            lab.fact(name, f"{count} 行  {path.name}")
        else:
            lab.fact(name, "未生成")

    if paths.CORPUS_DIR.exists():
        docx = docx_extract.find_corpus_docx(paths.CORPUS_DIR)
        lab.fact("corpus", docx.name if docx else "（空）")
    return 0
