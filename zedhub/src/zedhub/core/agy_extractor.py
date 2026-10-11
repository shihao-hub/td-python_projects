"""Antigravity (agy) 会话正文提取器：基于原生 transcript.jsonl 解析。

数据源（覆盖四大变体）：
- ~/.gemini/antigravity-acp/brain/<id>/.system_generated/logs/transcript.jsonl (Zed 插件)
- ~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript.jsonl (桌面版)
- ~/.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl (CLI 版)
- ~/.gemini/antigravity-ide/brain/<id>/.system_generated/logs/transcript.jsonl (IDE 版)

提取规则：
- USER_INPUT：提取 <USER_REQUEST> 标签内的纯文本作为 User 消息
- PLANNER_RESPONSE：提取 content 字段作为 Assistant 消息
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .agent_paths import ANTIGRAVITY_VARIANTS, agent_data_root
from .model import Message, MessagePart, parse_ts


def _brain_transcript_path(conversation_id: str, *, home: Path | None = None) -> Path | None:
    """在 Antigravity 各变体实例中定位 transcript.jsonl。"""
    root = agent_data_root("antigravity", home=home)
    for variant in ANTIGRAVITY_VARIANTS:
        candidate = (
            root / variant / "brain" / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"
        )
        if candidate.is_file():
            return candidate
    return None


def extract_agy_from_transcript(path: Path) -> list[Message]:
    """从 transcript.jsonl 提取纯文本对话气泡。

    提取 USER_INPUT 中 <USER_REQUEST> 标签内的文本作为 User 消息，
    提取 PLANNER_RESPONSE 中 content 字段作为 Assistant 消息。
    """
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

            step_type = obj.get("type")
            created_at = parse_ts(obj.get("created_at"))

            if step_type == "USER_INPUT":
                raw = obj.get("content", "")
                text = _extract_user_request(raw)
                if text:
                    idx += 1
                    messages.append(
                        Message(
                            id=f"agy_tr_msg_{idx}",
                            role="user",
                            created_at=created_at,
                            parts=[MessagePart(id=f"agy_tr_prt_{idx}_0", type="text", text=text)],
                        )
                    )
            elif step_type == "PLANNER_RESPONSE":
                content = obj.get("content", "")
                if isinstance(content, str) and content.strip():
                    idx += 1
                    messages.append(
                        Message(
                            id=f"agy_tr_msg_{idx}",
                            role="assistant",
                            created_at=created_at,
                            parts=[MessagePart(id=f"agy_tr_prt_{idx}_0", type="text", text=content.strip())],
                        )
                    )
    return messages


def _extract_user_request(raw: str) -> str:
    """从 USER_INPUT content 中提取 <USER_REQUEST> 标签内的纯文本。"""
    if not isinstance(raw, str):
        return ""
    match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw.strip()


def extract_agy_messages(
    conversation_id: str,
    *,
    home: Path | None = None,
) -> list[Message]:
    """Antigravity 会话正文提取：仅走 transcript.jsonl，未命中返回空列表。"""
    transcript = _brain_transcript_path(conversation_id, home=home)
    if transcript is not None:
        return extract_agy_from_transcript(transcript)
    return []
