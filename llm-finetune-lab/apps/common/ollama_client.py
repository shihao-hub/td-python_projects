"""极简 Ollama HTTP 客户端：只用标准库，方便离线与排障。

用途：数据工厂的 QA 合成、评测裁判、RAG 对照的基座推理。
默认地址 http://127.0.0.1:11434，可用 OLLAMA_HOST 覆盖。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


class OllamaError(RuntimeError):
    """Ollama 不可用或返回错误。"""


def _request(host: str, method: str, path: str, payload: dict | None, timeout: float) -> dict:
    url = f"{host.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise OllamaError(f"Ollama 请求失败（{url}）: {exc}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Ollama 返回非 JSON（{url}）: {raw[:200]}") from exc


def list_models(host: str = DEFAULT_HOST, timeout: float = 5.0) -> list[dict[str, Any]]:
    """列出本机已拉取的模型。不可用时抛 OllamaError。"""
    data = _request(host, "GET", "/api/tags", None, timeout)
    return data.get("models", [])


def is_available(host: str = DEFAULT_HOST, timeout: float = 3.0) -> bool:
    try:
        list_models(host, timeout)
        return True
    except OllamaError:
        return False


def chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    host: str = DEFAULT_HOST,
    temperature: float = 0.0,
    num_ctx: int = 4096,
    num_predict: int = 512,
    fmt: dict | str | None = None,
    timeout: float = 600.0,
) -> str:
    """一次性对话（非流式），返回 assistant 文本。

    fmt 传 "json" 或 JSON Schema 时，Ollama 会强制结构化输出（数据工厂用它生成 QA 对）。
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    if fmt is not None:
        payload["format"] = fmt
    data = _request(host, "POST", "/api/chat", payload, timeout)
    message = data.get("message") or {}
    content = message.get("content", "")
    if not content:
        raise OllamaError(f"Ollama 返回空内容（model={model}），原始响应: {str(data)[:300]}")
    return content


def embed(
    model: str,
    text: str,
    *,
    host: str = DEFAULT_HOST,
    timeout: float = 120.0,
) -> list[float]:
    """文本向量（RAG 对照实验用），需要本机已拉取 embedding 模型。"""
    data = _request(host, "POST", "/api/embed", {"model": model, "input": text}, timeout)
    embeddings = data.get("embeddings") or []
    if not embeddings:
        raise OllamaError(f"Ollama 未返回向量（model={model}）")
    return embeddings[0]
