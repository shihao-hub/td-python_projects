"""多源 Agent 本地会话正文提取器。

从 Claude Code / Codex / Pi 的原始 JSONL 会话文件中提取纯净的高层人机对话气泡
（类似微信/Slack，仅包含 user 和 assistant 的纯文本消息，过滤掉工具调用、终端执行结果与思考过程）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import Message, MessagePart, parse_ts


def _is_tool_result_content(content: Any) -> bool:
    """检查 content 列表是否全部由 tool_result 组成（Claude Code 将工具返回作为 user 发回）。"""
    if not isinstance(content, list):
        return False
    return any(
        isinstance(p, dict) and (p.get("type") == "tool_result" or "tool_use_id" in p)
        for p in content
    )


def extract_claude_messages(path: Path) -> list[Message]:
    """从 Claude Code JSONL 提取纯文本对话气泡。"""
    messages: list[Message] = []
    idx = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            t = obj.get("type")
            ts = obj.get("timestamp")
            created_at = parse_ts(ts) if isinstance(ts, str) else None

            if t == "user":
                msg = obj.get("message", {})
                content = msg.get("content") if isinstance(msg, dict) else None
                # 跳过纯工具返回帧
                if _is_tool_result_content(content):
                    continue
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = [
                        p.get("text", "")
                        for p in content
                        if isinstance(p, dict)
                        and p.get("type") == "text"
                        and not str(p.get("text", "")).startswith("[Request interrupted")
                    ]
                    text = "\n".join(p for p in parts if p)
                text = text.strip()
                if text:
                    idx += 1
                    messages.append(
                        Message(
                            id=f"claude_msg_{idx}",
                            role="user",
                            created_at=created_at,
                            parts=[MessagePart(id=f"claude_prt_{idx}_0", type="text", text=text)],
                        )
                    )
            elif t == "assistant":
                msg = obj.get("message", {})
                content = msg.get("content") if isinstance(msg, dict) else None
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    # 仅保留 text 类型文本，过滤 tool_use 与 thinking
                    parts = [
                        p.get("text", "")
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    text = "\n".join(p for p in parts if p)
                text = text.strip()
                if text:
                    idx += 1
                    model_id = msg.get("model") if isinstance(msg, dict) else None
                    messages.append(
                        Message(
                            id=f"claude_msg_{idx}",
                            role="assistant",
                            model_id=str(model_id) if model_id else None,
                            created_at=created_at,
                            parts=[MessagePart(id=f"claude_prt_{idx}_0", type="text", text=text)],
                        )
                    )
    return messages


def extract_codex_messages(path: Path) -> list[Message]:
    """从 Codex JSONL 提取纯文本对话气泡。"""
    messages: list[Message] = []
    idx = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            t = obj.get("type")
            ts = obj.get("timestamp")
            created_at = parse_ts(ts) if isinstance(ts, str) else None

            if t == "response_item":
                payload = obj.get("payload", {})
                if isinstance(payload, dict) and payload.get("type") == "message":
                    role = "user" if payload.get("role") == "user" else "assistant"
                    content = payload.get("content", [])
                    if isinstance(content, list):
                        parts = [
                            p.get("text", "")
                            for p in content
                            if isinstance(p, dict) and p.get("text")
                        ]
                        text = " ".join(parts).strip()
                    elif isinstance(content, str):
                        text = content.strip()
                    else:
                        text = ""
                    if text:
                        idx += 1
                        messages.append(
                            Message(
                                id=f"codex_msg_{idx}",
                                role=role,
                                created_at=created_at,
                                parts=[MessagePart(id=f"codex_prt_{idx}_0", type="text", text=text)],
                            )
                        )
    return messages


def extract_pi_messages(path: Path) -> list[Message]:
    """从 Pi JSONL 提取纯文本对话气泡。"""
    messages: list[Message] = []
    idx = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            t = obj.get("type")
            ts = obj.get("timestamp")
            created_at = parse_ts(ts) if isinstance(ts, (str, int, float)) else None

            if t == "message":
                msg = obj.get("message", {})
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = msg.get("content")
                text = ""
                if isinstance(content, str):
                    text = content.strip()
                elif isinstance(content, list):
                    # 仅保留 text 类型文本，过滤 thinking、toolCall、toolResult
                    parts = [
                        p.get("text", "")
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    text = "\n".join(p for p in parts if p).strip()
                if text:
                    idx += 1
                    messages.append(
                        Message(
                            id=f"pi_msg_{idx}",
                            role=role,
                            created_at=created_at,
                            parts=[MessagePart(id=f"pi_prt_{idx}_0", type="text", text=text)],
                        )
                    )
    return messages
