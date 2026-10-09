"""Antigravity (agy) 会话正文提取器：方案 C + 方案 A 混合策略。

方案 C：从 ``~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript.jsonl``
读取原生 JSON 文本（当前 IDE 产生的会话）。

方案 A：从 ``~/.gemini/antigravity-acp/conversations/<id>.db`` 的 ``steps.step_payload``
读取 Protobuf 二进制 BLOB，启发式解码 Wire Format 提取纯文本对话气泡。

提取规则（Plan 48 逆向结论）：
- step_type=14 → User Prompt（Field #19 包含用户纯文本）
- step_type=15 → Assistant Message（Field #20.Sub #1 包含助手正文回复）
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .model import Message, MessagePart, parse_ts


# ===========================================================================
# 方案 C：transcript.jsonl 解析（零成本）
# ===========================================================================


def _brain_transcript_path(conversation_id: str, *, home: Path | None = None) -> Path | None:
    """尝试定位 Brain 日志文件；不存在则返回 None。"""
    base = (home or Path.home()) / ".gemini" / "antigravity" / "brain" / conversation_id
    candidate = base / ".system_generated" / "logs" / "transcript.jsonl"
    return candidate if candidate.is_file() else None


def extract_agy_from_transcript(path: Path) -> list[Message]:
    """从 transcript.jsonl 提取纯文本对话气泡。

    提取 USER_INPUT 中 ``<USER_REQUEST>`` 标签内的文本作为 User 消息，
    提取 PLANNER_RESPONSE 中 ``content`` 字段（无 tool_calls 的纯回复步骤）作为 Assistant 消息。
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
                # 提取 <USER_REQUEST>...</USER_REQUEST> 标签内的纯文本
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
                # 仅保留有正文内容且不是纯工具调用的步骤
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
    """从 USER_INPUT content 中提取 <USER_REQUEST> 标签内的纯文本。

    content 通常包含 ``<USER_REQUEST>...</USER_REQUEST>`` 和
    ``<ADDITIONAL_METADATA>...</ADDITIONAL_METADATA>`` 等包装标签。
    我们只要纯粹的用户请求文本。
    """
    import re
    match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    # 兜底：无标签时取全文（极少出现）
    return raw.strip()


# ===========================================================================
# 方案 A：Protobuf Wire Format 启发式解码
# ===========================================================================


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """解码 Protobuf Varint，返回 (value, new_offset)。"""
    result = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        result |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            return result, offset
        shift += 7
    raise ValueError("Varint 未终止")


def _parse_proto_fields(data: bytes) -> dict[int, list[Any]]:
    """递归解析 Protobuf Wire Format，返回 {field_number: [values...]}。

    仅处理 Varint (0)、Length-delimited (2)、Fixed64 (1)、Fixed32 (5)。
    Length-delimited 字段尝试递归解析为子消息；失败则作为 bytes 保留。
    """
    fields: dict[int, list[Any]] = {}
    offset = 0
    while offset < len(data):
        try:
            tag, offset = _decode_varint(data, offset)
        except ValueError:
            break
        wire_type = tag & 0x07
        field_num = tag >> 3
        if field_num == 0:
            break  # 非法字段号

        if wire_type == 0:  # Varint
            value, offset = _decode_varint(data, offset)
            fields.setdefault(field_num, []).append(value)
        elif wire_type == 2:  # Length-delimited
            length, offset = _decode_varint(data, offset)
            if offset + length > len(data):
                break
            chunk = data[offset:offset + length]
            offset += length
            parsed = False
            # 对已知嵌套子消息字段（如 19 用户 prompt、20 助手回复）优先尝试递归解析
            if field_num in (19, 20):
                try:
                    sub = _parse_proto_fields(chunk)
                    if sub:
                        fields.setdefault(field_num, []).append(sub)
                        parsed = True
                except Exception:
                    pass
            if not parsed:
                # 尝试 UTF-8 解码为字符串
                try:
                    text = chunk.decode("utf-8")
                    fields.setdefault(field_num, []).append(text)
                except UnicodeDecodeError:
                    # 尝试递归解析为子消息
                    try:
                        sub = _parse_proto_fields(chunk)
                        if sub:
                            fields.setdefault(field_num, []).append(sub)
                        else:
                            fields.setdefault(field_num, []).append(chunk)
                    except Exception:
                        fields.setdefault(field_num, []).append(chunk)
        elif wire_type == 1:  # Fixed64
            if offset + 8 > len(data):
                break
            offset += 8
        elif wire_type == 5:  # Fixed32
            if offset + 4 > len(data):
                break
            offset += 4
        else:
            break  # 未知 wire type，停止解析
    return fields


def _extract_longest_text(fields: dict[int, list[Any]], target_field: int) -> str:
    """从指定字段号中提取最长的文本内容。"""
    candidates: list[str] = []
    for value in fields.get(target_field, []):
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
        elif isinstance(value, dict):
            # 递归搜索子消息中的文本
            for sub_values in value.values():
                for sv in sub_values:
                    if isinstance(sv, str) and sv.strip():
                        candidates.append(sv.strip())
    if not candidates:
        return ""
    return max(candidates, key=len)


def _extract_user_text_from_proto(fields: dict[int, list[Any]]) -> str:
    """从 step_type=14 的 Protobuf 字段中提取用户输入文本。

    Plan 48 逆向结论：Field #19 包含用户 Prompt 的嵌套消息。
    """
    return _extract_longest_text(fields, 19)


def _extract_assistant_text_from_proto(fields: dict[int, list[Any]]) -> str:
    """从 step_type=15 的 Protobuf 字段中提取助手回复文本。

    Plan 48 逆向结论：Field #20 的 Sub #1 包含助手正文（Markdown）。
    """
    for value in fields.get(20, []):
        if isinstance(value, dict):
            # Sub #1 是正文 Markdown
            text = _extract_longest_text(value, 1)
            if text:
                return text
        elif isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_agy_from_sqlite(db_path: Path) -> list[Message]:
    """从 Antigravity SQLite 数据库启发式提取纯文本对话气泡。"""
    messages: list[Message] = []
    idx = 0
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = conn.execute(
            "SELECT idx, step_type, step_payload FROM steps "
            "WHERE step_type IN (14, 15) AND step_payload IS NOT NULL "
            "ORDER BY idx"
        )
        for row_idx, step_type, payload in cursor:
            if not isinstance(payload, (bytes, bytearray)) or len(payload) < 4:
                continue
            try:
                fields = _parse_proto_fields(payload)
            except Exception:
                continue

            if step_type == 14:
                text = _extract_user_text_from_proto(fields)
                if text:
                    idx += 1
                    messages.append(
                        Message(
                            id=f"agy_pb_msg_{idx}",
                            role="user",
                            parts=[MessagePart(id=f"agy_pb_prt_{idx}_0", type="text", text=text)],
                        )
                    )
            elif step_type == 15:
                text = _extract_assistant_text_from_proto(fields)
                if text:
                    idx += 1
                    messages.append(
                        Message(
                            id=f"agy_pb_msg_{idx}",
                            role="assistant",
                            parts=[MessagePart(id=f"agy_pb_prt_{idx}_0", type="text", text=text)],
                        )
                    )
    finally:
        conn.close()
    return messages


# ===========================================================================
# 混合提取入口
# ===========================================================================


def extract_agy_messages(
    conversation_id: str,
    *,
    home: Path | None = None,
) -> list[Message]:
    """Antigravity 会话正文提取：方案 C 优先、方案 A 兜底。

    1. 优先尝试 Brain 日志 transcript.jsonl（当前 IDE 会话，JSON 原生格式）；
    2. 不存在时回退到 SQLite + Proto 启发式提取（其他来源的会话）。
    """
    # 方案 C：transcript.jsonl
    transcript = _brain_transcript_path(conversation_id, home=home)
    if transcript is not None:
        messages = extract_agy_from_transcript(transcript)
        if messages:
            return messages

    # 方案 A：SQLite + Proto 启发式提取
    conversations_dir = (home or Path.home()) / ".gemini" / "antigravity-acp" / "conversations"
    db_path = conversations_dir / f"{conversation_id}.db"
    if db_path.is_file():
        return extract_agy_from_sqlite(db_path)

    return []
