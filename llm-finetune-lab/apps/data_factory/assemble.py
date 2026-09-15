"""组装 SFT 数据集：QA 样本 + 条文背诵样本 + 拒答样本 → 统一 chat 格式。

输出每行：
{"id", "type": "qa"|"quote"|"refusal", "chunk_id", "messages": [system, user, assistant]}
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from apps.common import prompts

# 条文编号：第X条（支持中文数字与阿拉伯数字）
_CLAUSE_RE = re.compile(r"第([一二三四五六七八九十百千零〇0-9]+)条")
# 条文正文：从「第X条」起，到句号成段（贪心到下一条或 200 字）
_CLAUSE_BODY_RE = re.compile(r"(第[一二三四五六七八九十百千零〇0-9]+条[^。{]*。)", re.DOTALL)


def _normalize_question(q: str) -> str:
    """去空白与标点，用于精确去重。"""
    return re.sub(r"[\s，。？！?!,.·、\"'“”‘’（）()【】\[\]]+", "", q)


def build_quote_samples(chunks: list[dict], max_per_chunk: int = 2) -> list[dict]:
    """从 chunk 原文抽取「第X条」生成背诵样本（不依赖 Ollama，零成本高质量）。"""
    samples: list[dict] = []
    for chunk in chunks:
        found = _CLAUSE_BODY_RE.findall(chunk["text"])
        for clause_text in found[:max_per_chunk]:
            m = _CLAUSE_RE.search(clause_text)
            if not m:
                continue
            no = m.group(1)
            templates = prompts.QUOTE_QUESTION_TEMPLATES
            question = templates[len(samples) % len(templates)].format(no=no)
            answer = clause_text.strip()
            samples.append(
                {
                    "id": f"quote-{chunk['chunk_id']}-{no}",
                    "type": "quote",
                    "chunk_id": chunk["chunk_id"],
                    "messages": [
                        {"role": "system", "content": prompts.SYSTEM_PROMPT},
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": answer},
                    ],
                }
            )
    return samples


def build_qa_samples(qa_records: list[dict]) -> list[dict]:
    samples: list[dict] = []
    for record in qa_records:
        for j, pair in enumerate(record.get("qa", [])):
            samples.append(
                {
                    "id": f"qa-{record['chunk_id']}-{j}",
                    "type": "qa",
                    "chunk_id": record["chunk_id"],
                    "messages": [
                        {"role": "system", "content": prompts.SYSTEM_PROMPT},
                        {"role": "user", "content": pair["question"]},
                        {"role": "assistant", "content": pair["answer"]},
                    ],
                }
            )
    return samples


def build_refusal_samples(qa_records: list[dict], static_pool: list[str]) -> list[dict]:
    """拒答样本 = 静态问题池 + LLM 生成的边界问题（uncovered）。"""
    questions = list(static_pool)
    for record in qa_records:
        questions.extend(record.get("uncovered", []))

    seen: set[str] = set()
    samples: list[dict] = []
    for i, question in enumerate(questions):
        key = _normalize_question(question)
        if not key or key in seen:
            continue
        seen.add(key)
        samples.append(
            {
                "id": f"refusal-{i}",
                "type": "refusal",
                "chunk_id": None,
                "messages": [
                    {"role": "system", "content": prompts.SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": prompts.REFUSAL_REPLY},
                ],
            }
        )
    return samples


def dedup(samples: list[dict]) -> tuple[list[dict], int]:
    """按归一化问题去重（qa/quote/refusal 统一处理，先到先得）。"""
    seen: set[str] = set()
    result: list[dict] = []
    dropped = 0
    for sample in samples:
        question = next(m["content"] for m in sample["messages"] if m["role"] == "user")
        key = _normalize_question(question)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        result.append(sample)
    return result, dropped


def assemble(qa_path: Path | None, chunks: list[dict]) -> dict:
    """主入口：返回 (样本列表, 统计)。"""
    qa_records: list[dict] = []
    if qa_path is not None and qa_path.exists():
        with qa_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "error" not in record:
                    qa_records.append(record)

    qa_samples = build_qa_samples(qa_records)
    quote_samples = build_quote_samples(chunks)
    refusal_samples = build_refusal_samples(qa_records, prompts.REFUSAL_POOL)

    all_samples, dropped = dedup(qa_samples + quote_samples + refusal_samples)
    by_type: dict[str, int] = {}
    for s in all_samples:
        by_type[s["type"]] = by_type.get(s["type"], 0) + 1
    return {"samples": all_samples, "dropped": dropped, "by_type": by_type}
