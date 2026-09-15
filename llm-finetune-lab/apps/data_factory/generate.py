"""QA 合成：把每个 chunk 交给本地 Ollama 模型生成问答对 + 边界问题。

断点续跑：输出文件按行追加，启动时先读已有 chunk_id 跳过；
单块失败重试 2 次，仍失败则记录 error 行（不中断整体），最后统计可重跑。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from apps.common import ollama_client, prompts


@dataclass
class GenResult:
    chunk_id: str
    section_path: str
    qa: list[dict]  # [{"question", "answer"}]
    uncovered: list[str]  # 边界问题（无答案，用于拒答训练）

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "section_path": self.section_path,
            "qa": self.qa,
            "uncovered": self.uncovered,
        }


def _parse_json_loose(raw: str) -> dict:
    """Ollama format=json 已保证合法 JSON，这里只做剥壳容错。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _generate_for_chunk(chunk: dict, model: str, qa_per_chunk: int, with_uncovered: bool) -> GenResult:
    qa_prompt = prompts.QA_GEN_TEMPLATE.format(
        n=qa_per_chunk, section=chunk["section_path"], text=chunk["text"]
    )
    raw = ollama_client.chat(
        model,
        [{"role": "user", "content": qa_prompt}],
        fmt="json",
        temperature=0.7,
        num_ctx=4096,
        num_predict=1024,
        timeout=300,
    )
    data = _parse_json_loose(raw)
    qa = [
        {"question": str(item.get("question", "")).strip(), "answer": str(item.get("answer", "")).strip()}
        for item in data.get("qa", [])
        if item.get("question") and item.get("answer")
    ]

    uncovered: list[str] = []
    if with_uncovered:
        unc_prompt = prompts.UNCOVERED_GEN_TEMPLATE.format(
            n=2, section=chunk["section_path"], text=chunk["text"]
        )
        raw2 = ollama_client.chat(
            model,
            [{"role": "user", "content": unc_prompt}],
            fmt="json",
            temperature=0.7,
            num_ctx=4096,
            num_predict=256,
            timeout=300,
        )
        data2 = _parse_json_loose(raw2)
        uncovered = [str(q).strip() for q in data2.get("questions", []) if str(q).strip()]

    return GenResult(chunk["chunk_id"], chunk["section_path"], qa, uncovered)


def load_done_chunk_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("chunk_id"):
                done.add(record["chunk_id"])
    return done


def generate_qa(
    chunks: list[dict],
    out_path: Path,
    model: str,
    qa_per_chunk: int = 2,
    limit: int | None = None,
    with_uncovered: bool = True,
    retries: int = 2,
) -> dict:
    """增量生成，返回统计信息。Ctrl+C 随时可中断，下次续跑。"""
    done = load_done_chunk_ids(out_path)
    todo = [c for c in chunks if c["chunk_id"] not in done]
    if limit is not None:
        todo = todo[:limit]
    total_all = len(chunks)
    print(f"  已完成 {len(done)}/{total_all} 块，本次待生成 {len(todo)} 块（模型 {model}）")

    if todo:
        # 先确认 Ollama 在线，避免循环里反复失败
        names = [m["name"] for m in ollama_client.list_models()]
        if not any(n == model or n.startswith(model + ":") for n in names):
            raise SystemExit(f"!! Ollama 中没有模型 {model}，先执行: ollama pull {model}")

    errors = 0
    t0 = time.perf_counter()
    with out_path.open("a", encoding="utf-8") as fh:
        for i, chunk in enumerate(todo, 1):
            last_err: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    result = _generate_for_chunk(chunk, model, qa_per_chunk, with_uncovered)
                    fh.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
                    fh.flush()
                    last_err = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    time.sleep(2 * (attempt + 1))
            if last_err is not None:
                errors += 1
                fh.write(
                    json.dumps(
                        {"chunk_id": chunk["chunk_id"], "section_path": chunk["section_path"], "error": repr(last_err)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                fh.flush()

            if i % 10 == 0 or i == len(todo):
                elapsed = time.perf_counter() - t0
                eta = elapsed / i * (len(todo) - i)
                qa_count = qa_per_chunk
                print(
                    f"  [{i}/{len(todo)}] {chunk['section_path'][:30]} "
                    f"| {elapsed / i:.1f}s/块 | 剩余约 {eta / 60:.0f} 分钟"
                    f"（{qa_count} QA/块）| 错误 {errors}"
                )

    return {"done_before": len(done), "generated": len(todo) - errors, "errors": errors, "total": total_all}
