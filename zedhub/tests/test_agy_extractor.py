"""Antigravity 会话正文提取器单元测试。

覆盖：
- 方案 C：transcript.jsonl 解析与 USER_REQUEST 标签提取
- 方案 A：Protobuf Wire Format Varint 解码器正确性
- 方案 A：step_type=14/15 字段提取
- 混合路径：方案 C 优先、方案 A 兜底回退
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from zedhub.core.agy_extractor import (
    _decode_varint,
    _extract_user_request,
    _parse_proto_fields,
    extract_agy_from_sqlite,
    extract_agy_from_transcript,
    extract_agy_messages,
)


def test_decode_varint_basic() -> None:
    """Varint 解码器：基本值。"""
    # 单字节 Varint：值 = 1
    val, offset = _decode_varint(b"\x01", 0)
    assert val == 1
    assert offset == 1

    # 单字节 Varint：值 = 127
    val, offset = _decode_varint(b"\x7f", 0)
    assert val == 127
    assert offset == 1

    # 双字节 Varint：值 = 150 = 0x96 0x01
    val, offset = _decode_varint(b"\x96\x01", 0)
    assert val == 150
    assert offset == 2

    # 双字节 Varint：值 = 300 = 0xAC 0x02
    val, offset = _decode_varint(b"\xac\x02", 0)
    assert val == 300
    assert offset == 2


def test_decode_varint_with_offset() -> None:
    """Varint 解码器：带偏移量。"""
    data = b"\x00\x00\x96\x01\xff"
    val, offset = _decode_varint(data, 2)
    assert val == 150
    assert offset == 4


def test_parse_proto_fields_simple_varint() -> None:
    """Proto 解析器：简单 Varint 字段。"""
    # Field 1, wire type 0, value 15 → tag = (1 << 3) | 0 = 0x08, value = 0x0f
    data = b"\x08\x0f"
    fields = _parse_proto_fields(data)
    assert 1 in fields
    assert fields[1] == [15]


def test_parse_proto_fields_string() -> None:
    """Proto 解析器：Length-delimited 字符串字段。"""
    text = "Hello"
    text_bytes = text.encode("utf-8")
    # Field 2, wire type 2 → tag = (2 << 3) | 2 = 0x12
    data = b"\x12" + bytes([len(text_bytes)]) + text_bytes
    fields = _parse_proto_fields(data)
    assert 2 in fields
    assert fields[2] == ["Hello"]


def test_extract_user_request_tag() -> None:
    """从 USER_INPUT content 中提取 <USER_REQUEST> 标签内容。"""
    raw = (
        "<USER_REQUEST>\n你好，请帮我看一下代码\n</USER_REQUEST>\n"
        "<ADDITIONAL_METADATA>\nlocal time is: 2026-10-07\n</ADDITIONAL_METADATA>"
    )
    result = _extract_user_request(raw)
    assert result == "你好，请帮我看一下代码"


def test_extract_user_request_no_tag() -> None:
    """无标签时取全文。"""
    raw = "直接的用户输入"
    result = _extract_user_request(raw)
    assert result == "直接的用户输入"


def test_extract_from_transcript(tmp_path: Path) -> None:
    """方案 C：从 transcript.jsonl 提取纯文本气泡。"""
    transcript = tmp_path / "transcript.jsonl"
    lines = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-01T10:00:00Z",
            "content": "<USER_REQUEST>\n帮我写一个函数\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\ntime: now\n</ADDITIONAL_METADATA>",
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-01T10:00:05Z",
            "content": "好的，我来帮你写一个函数。",
            "tool_calls": [],
        },
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-01T10:00:10Z",
            "content": "",  # 纯工具调用步骤，无正文
            "tool_calls": [{"name": "write_to_file", "args": {}}],
        },
        {
            "step_index": 3,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-01T10:00:15Z",
            "content": "函数已经写好了，请查看。",
        },
    ]
    with transcript.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    msgs = extract_agy_from_transcript(transcript)
    assert len(msgs) == 3
    assert msgs[0].role == "user"
    assert msgs[0].parts[0].text == "帮我写一个函数"
    assert msgs[1].role == "assistant"
    assert msgs[1].parts[0].text == "好的，我来帮你写一个函数。"
    assert msgs[2].role == "assistant"
    assert msgs[2].parts[0].text == "函数已经写好了，请查看。"


def _make_varint(value: int) -> bytes:
    """编码整数为 Protobuf Varint。"""
    result = bytearray()
    while value > 127:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _make_proto_field(field_num: int, wire_type: int, data: bytes) -> bytes:
    """构建一个 Protobuf 字段。"""
    tag = _make_varint((field_num << 3) | wire_type)
    if wire_type == 0:  # Varint
        return tag + data
    elif wire_type == 2:  # Length-delimited
        return tag + _make_varint(len(data)) + data
    raise ValueError(f"不支持的 wire_type: {wire_type}")


def test_extract_from_sqlite(tmp_path: Path) -> None:
    """方案 A：从 SQLite + Proto 中启发式提取纯文本气泡。"""
    db_path = tmp_path / "test_agy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE steps ("
        "  idx INTEGER PRIMARY KEY,"
        "  step_type INTEGER NOT NULL DEFAULT 0,"
        "  status INTEGER NOT NULL DEFAULT 0,"
        "  has_subtrajectory NUMERIC NOT NULL DEFAULT 0,"
        "  metadata BLOB,"
        "  error_details BLOB,"
        "  permissions BLOB,"
        "  task_details BLOB,"
        "  render_info BLOB,"
        "  step_payload BLOB,"
        "  step_format INTEGER NOT NULL DEFAULT 0"
        ")"
    )

    # 构造 step_type=14（User Prompt）：Field #19 包含用户文本
    user_text = "你好，请帮我分析这段代码"
    user_text_bytes = user_text.encode("utf-8")
    user_payload = (
        _make_proto_field(1, 0, _make_varint(14))  # step_type = 14
        + _make_proto_field(19, 2, user_text_bytes)  # Field #19 = 用户文本
    )

    # 构造 step_type=15（Assistant Message）：Field #20 > Sub #1 包含助手正文
    assistant_text = "好的，我来分析这段代码。"
    assistant_text_bytes = assistant_text.encode("utf-8")
    # 子消息：Sub #1 = 正文 Markdown
    sub_message = _make_proto_field(1, 2, assistant_text_bytes)
    assistant_payload = (
        _make_proto_field(1, 0, _make_varint(15))  # step_type = 15
        + _make_proto_field(20, 2, sub_message)  # Field #20 = 嵌套子消息
    )

    # 构造 step_type=17（Tool Call）：应被过滤
    tool_payload = (
        _make_proto_field(1, 0, _make_varint(17))
        + _make_proto_field(116, 2, b'{"tool": "run_command"}')
    )

    conn.execute(
        "INSERT INTO steps (idx, step_type, step_payload) VALUES (?, ?, ?)",
        (0, 14, user_payload),
    )
    conn.execute(
        "INSERT INTO steps (idx, step_type, step_payload) VALUES (?, ?, ?)",
        (1, 15, assistant_payload),
    )
    conn.execute(
        "INSERT INTO steps (idx, step_type, step_payload) VALUES (?, ?, ?)",
        (2, 17, tool_payload),
    )
    conn.commit()
    conn.close()

    msgs = extract_agy_from_sqlite(db_path)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].parts[0].text == user_text
    assert msgs[1].role == "assistant"
    assert msgs[1].parts[0].text == assistant_text


def test_hybrid_prefers_transcript(tmp_path: Path) -> None:
    """混合策略：transcript.jsonl 存在时优先使用方案 C。"""
    conv_id = "test-hybrid-00000000"

    # 创建方案 C 的 transcript.jsonl
    brain_dir = tmp_path / ".gemini" / "antigravity" / "brain" / conv_id / ".system_generated" / "logs"
    brain_dir.mkdir(parents=True)
    transcript = brain_dir / "transcript.jsonl"
    lines = [
        {
            "type": "USER_INPUT",
            "created_at": "2026-10-01T10:00:00Z",
            "content": "<USER_REQUEST>\n来自 transcript\n</USER_REQUEST>",
        },
        {
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-01T10:00:05Z",
            "content": "这是来自 transcript 的回复。",
        },
    ]
    with transcript.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    # 同时创建方案 A 的 SQLite DB（应被忽略）
    conv_dir = tmp_path / ".gemini" / "antigravity-acp" / "conversations"
    conv_dir.mkdir(parents=True)
    db_path = conv_dir / f"{conv_id}.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE steps ("
        "  idx INTEGER PRIMARY KEY,"
        "  step_type INTEGER NOT NULL DEFAULT 0,"
        "  status INTEGER NOT NULL DEFAULT 0,"
        "  has_subtrajectory NUMERIC NOT NULL DEFAULT 0,"
        "  metadata BLOB, error_details BLOB, permissions BLOB,"
        "  task_details BLOB, render_info BLOB, step_payload BLOB,"
        "  step_format INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    user_payload = (
        _make_proto_field(1, 0, _make_varint(14))
        + _make_proto_field(19, 2, "来自 SQLite 的消息".encode("utf-8"))
    )
    conn.execute("INSERT INTO steps (idx, step_type, step_payload) VALUES (0, 14, ?)", (user_payload,))
    conn.commit()
    conn.close()

    # 混合提取：应使用方案 C 的结果
    msgs = extract_agy_messages(conv_id, home=tmp_path)
    assert len(msgs) == 2
    assert msgs[0].parts[0].text == "来自 transcript"
    assert msgs[1].parts[0].text == "这是来自 transcript 的回复。"


def test_hybrid_falls_back_to_sqlite(tmp_path: Path) -> None:
    """混合策略：无 transcript.jsonl 时回退到方案 A。"""
    conv_id = "test-fallback-00000000"

    # 只创建方案 A 的 SQLite DB
    conv_dir = tmp_path / ".gemini" / "antigravity-acp" / "conversations"
    conv_dir.mkdir(parents=True)
    db_path = conv_dir / f"{conv_id}.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE steps ("
        "  idx INTEGER PRIMARY KEY,"
        "  step_type INTEGER NOT NULL DEFAULT 0,"
        "  status INTEGER NOT NULL DEFAULT 0,"
        "  has_subtrajectory NUMERIC NOT NULL DEFAULT 0,"
        "  metadata BLOB, error_details BLOB, permissions BLOB,"
        "  task_details BLOB, render_info BLOB, step_payload BLOB,"
        "  step_format INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    user_payload = (
        _make_proto_field(1, 0, _make_varint(14))
        + _make_proto_field(19, 2, "SQLite 兜底提取".encode("utf-8"))
    )
    assistant_text = "这是从 Proto 解析出来的回复"
    sub_msg = _make_proto_field(1, 2, assistant_text.encode("utf-8"))
    asst_payload = (
        _make_proto_field(1, 0, _make_varint(15))
        + _make_proto_field(20, 2, sub_msg)
    )
    conn.execute("INSERT INTO steps (idx, step_type, step_payload) VALUES (0, 14, ?)", (user_payload,))
    conn.execute("INSERT INTO steps (idx, step_type, step_payload) VALUES (1, 15, ?)", (asst_payload,))
    conn.commit()
    conn.close()

    msgs = extract_agy_messages(conv_id, home=tmp_path)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].parts[0].text == "SQLite 兜底提取"
    assert msgs[1].role == "assistant"
    assert msgs[1].parts[0].text == assistant_text
