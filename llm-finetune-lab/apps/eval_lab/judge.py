"""评测裁判与指标计算：本地 Ollama 当 LLM-as-judge，四种被测配置共用。

- qa / quote 样本：裁判对比「参考答案 vs 模型答案」→ correct / partial / wrong / refused
- refusal 样本：模型拒答 = 正确；编造条款回答 = 幻觉
"""

from __future__ import annotations

import json

from apps.common import ollama_client, prompts

JUDGE_MODEL_DEFAULT = "qwen2.5:7b"

JUDGE_TEMPLATE = """你是评测裁判。根据【参考答案】判断【模型答案】的质量。

判断标准：
- correct：关键信息（数值、期限、条件、条款号）与参考一致；
- partial：方向正确但关键信息缺失或不完整；
- wrong：与参考矛盾，或编造了参考中不存在的条款/数值；
- refused：模型明确表示没有相关规定 / 拒答。

只输出 JSON：
{{"verdict": "correct|partial|wrong|refused", "reason": "一句话理由"}}

【问题】{question}
【参考答案】{reference}
【模型答案】{model_answer}"""


def parse_refusal_answer(answer: str) -> bool:
    """拒答识别：不依赖裁判（拒答话术是我们自己训练时定死的）。"""
    markers = ("未找到相关规定", "没有相关规定", "未作规定", "无法回答", "没有涉及", "未涉及")
    return any(m in answer for m in markers)


def judge_one(question: str, reference: str, model_answer: str, judge_model: str) -> dict:
    raw = ollama_client.chat(
        judge_model,
        [{"role": "user", "content": JUDGE_TEMPLATE.format(
            question=question, reference=reference, model_answer=model_answer
        )}],
        fmt="json",
        temperature=0.0,
        num_ctx=2048,
        num_predict=128,
        timeout=120,
    )
    try:
        data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        verdict = str(data.get("verdict", "wrong")).strip().lower()
        if verdict not in ("correct", "partial", "wrong", "refused"):
            verdict = "wrong"
        return {"verdict": verdict, "reason": str(data.get("reason", ""))[:120]}
    except (json.JSONDecodeError, AttributeError):
        return {"verdict": "wrong", "reason": "裁判输出解析失败"}


def verdict_for_sample(sample: dict, answer: str, judge_model: str) -> dict:
    """按样本类型选择判分路径。"""
    question = next(m["content"] for m in sample["messages"] if m["role"] == "user")
    reference = next(m["content"] for m in sample["messages"] if m["role"] == "assistant")

    if sample["type"] == "refusal":
        refused = parse_refusal_answer(answer)
        hallucinated = (not refused) and len(answer.strip()) > 15
        return {
            "verdict": "correct" if refused else "hallucinated",
            "reason": "模型正确拒答" if refused else "应当拒答但模型给出了实质回答（幻觉）",
        }

    if parse_refusal_answer(answer):
        return {"verdict": "refused", "reason": "文档有规定但模型拒答"}
    return judge_one(question, reference, answer, judge_model)


def aggregate(details: list[dict]) -> dict:
    """汇总指标：QA 准确率 / 拒答正确率 / 幻觉率 / 覆盖率。"""
    qa = [d for d in details if d["type"] in ("qa", "quote")]
    refusal = [d for d in details if d["type"] == "refusal"]

    def rate(items: list[dict], predicate) -> float:
        return round(sum(1 for d in items if predicate(d["verdict"])) / len(items), 4) if items else None

    return {
        "samples": len(details),
        "qa_total": len(qa),
        "qa_accuracy": rate(qa, lambda v: v in ("correct", "partial")),
        "qa_strict_accuracy": rate(qa, lambda v: v == "correct"),
        "qa_wronganswer_rate": rate(qa, lambda v: v == "wrong"),
        "qa_overrefusal_rate": rate(qa, lambda v: v == "refused"),
        "refusal_total": len(refusal),
        "refusal_correct": rate(refusal, lambda v: v == "correct"),
        "hallucination_rate": rate(refusal, lambda v: v == "hallucinated"),
    }
