"""RAG 检索与提示词组装测试（mock 向量，不依赖 Ollama）。"""

from __future__ import annotations

import numpy as np


def _fake_embed_matrix(chunks: list[dict]) -> np.ndarray:
    """按关键词构造可分向量：考勤类 → 第一维 1，报销类 → 第二维 1。"""
    vectors = []
    for chunk in chunks:
        text = chunk["text"]
        v = np.array([1.0 if "考勤" in text or "迟到" in text else 0.0,
                      1.0 if "报销" in text or "发票" in text else 0.0,
                      0.1], dtype=np.float32)
        vectors.append(v)
    matrix = np.asarray(vectors)
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def test_retrieve_ranks_by_similarity(monkeypatch) -> None:
    from apps.common import ollama_client
    from apps.rag_compare import rag

    chunks = [
        {"chunk_id": "c1", "section_path": "第一章", "text": "考勤管理：迟到三十分钟按旷工处理"},
        {"chunk_id": "c2", "section_path": "第二章", "text": "费用报销：发票应当在三十日内提交"},
        {"chunk_id": "c3", "section_path": "第二章", "text": "报销标准：住宿费上限六百元"},
    ]
    matrix = _fake_embed_matrix(chunks)

    def fake_embed(model, text, **kwargs):
        if "迟到" in text or "考勤" in text:
            return [1.0, 0.0, 0.1]
        return [0.0, 1.0, 0.1]

    monkeypatch.setattr(ollama_client, "embed", fake_embed)
    hits = rag.retrieve("迟到怎么处理？", "bge-m3", chunks, matrix, top_k=2)
    assert hits[0]["chunk_id"] == "c1"
    assert len(hits) == 2


def test_compose_rag_messages(monkeypatch) -> None:
    from apps.common import ollama_client, prompts
    from apps.rag_compare import rag

    monkeypatch.setattr(ollama_client, "embed", lambda model, text, **kw: [1.0, 0.0, 0.0])
    hits = [{"chunk_id": "c1", "section_path": "第一章", "text": "迟到按旷工处理", "score": 0.9}]
    messages = rag.compose_rag_messages("迟到怎么办？", hits, prompts.SYSTEM_PROMPT)
    assert messages[0]["role"] == "system"
    assert "迟到按旷工处理" in messages[0]["content"]
    assert "制度文档" in messages[0]["content"]
    assert messages[1]["content"] == "迟到怎么办？"
