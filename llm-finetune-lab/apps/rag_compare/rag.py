"""RAG 基础设施：块向量索引 + 余弦检索（纯 numpy，无第三方向量库依赖）。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from apps.common import ollama_client

INDEX_PATH_NAME = "rag_index.npz"


def load_chunks(chunks_path: Path) -> list[dict]:
    rows = []
    with chunks_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_index(chunks: list[dict], embed_model: str, index_path: Path, host: str | None = None) -> None:
    """全量重建向量索引（bge-m3 约 1700 块 × 0.1s ≈ 3 分钟）。"""
    vectors = []
    for i, chunk in enumerate(chunks, 1):
        text = f"{chunk['section_path']}\n{chunk['text']}"
        vec = ollama_client.embed(embed_model, text, host=host or ollama_client.DEFAULT_HOST)
        vectors.append(vec)
        if i % 50 == 0 or i == len(chunks):
            print(f"  索引进度 [{i}/{len(chunks)}]")
    matrix = np.asarray(vectors, dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8  # 归一化后点积即余弦
    np.savez_compressed(
        index_path,
        matrix=matrix,
        chunk_ids=np.array([c["chunk_id"] for c in chunks]),
    )


def load_index(index_path: Path) -> tuple[np.ndarray, dict[str, str]]:
    data = np.load(index_path, allow_pickle=False)
    return data["matrix"], {"ids": data["chunk_ids"].tolist()}


def retrieve(question: str, embed_model: str, chunks: list[dict], matrix: np.ndarray,
             top_k: int = 3, host: str | None = None) -> list[dict]:
    """top-k 检索：返回 [{chunk_id, section_path, text, score}]。"""
    query = np.asarray(
        ollama_client.embed(embed_model, question, host=host or ollama_client.DEFAULT_HOST),
        dtype=np.float32,
    )
    query /= np.linalg.norm(query) + 1e-8
    scores = matrix @ query
    top = np.argsort(-scores)[:top_k]
    results = []
    for idx in top:
        chunk = chunks[int(idx)]
        results.append({**chunk, "score": float(scores[int(idx)])})
    return results


def compose_rag_messages(question: str, hits: list[dict], system: str) -> list[dict[str, str]]:
    """RAG 提示词：检索片段进 system 上下文，要求只依据片段回答。"""
    context = "\n\n".join(
        f"【片段 {i}｜{h['section_path']}】\n{h['text']}" for i, h in enumerate(hits, 1)
    )
    rag_system = (
        system
        + "\n\n以下是制度文档检索片段，回答必须且只需依据这些片段：\n\n" + context
    )
    return [
        {"role": "system", "content": rag_system},
        {"role": "user", "content": question},
    ]
