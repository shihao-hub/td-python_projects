"""Antigravity 会话正文提取器单元测试。

覆盖：
- transcript.jsonl 原生解析与 USER_REQUEST 标签提取
- 多变体路径探测（antigravity-acp, antigravity, antigravity-cli, antigravity-ide）
- extract_agy_messages 主入口调用
"""

from __future__ import annotations

import json
from pathlib import Path

from zedhub.core.agy_extractor import (
    _brain_transcript_path,
    _extract_user_request,
    extract_agy_from_transcript,
    extract_agy_messages,
)


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
    """从 transcript.jsonl 提取纯文本气泡。"""
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


def test_brain_transcript_path_variants(tmp_path: Path) -> None:
    """验证四大变体路径均可被 _brain_transcript_path 正确探测。"""
    home = tmp_path
    gemini = home / ".gemini"

    # 1. 测试 antigravity-acp 优先
    acp_log = gemini / "antigravity-acp" / "brain" / "sid_acp" / ".system_generated" / "logs" / "transcript.jsonl"
    acp_log.parent.mkdir(parents=True, exist_ok=True)
    acp_log.write_text("{}", encoding="utf-8")

    found = _brain_transcript_path("sid_acp", home=home)
    assert found == acp_log

    # 2. 测试 antigravity 桌面版
    desktop_log = gemini / "antigravity" / "brain" / "sid_desk" / ".system_generated" / "logs" / "transcript.jsonl"
    desktop_log.parent.mkdir(parents=True, exist_ok=True)
    desktop_log.write_text("{}", encoding="utf-8")

    found = _brain_transcript_path("sid_desk", home=home)
    assert found == desktop_log

    # 3. 未找到时返回 None
    assert _brain_transcript_path("non_existent", home=home) is None


def test_extract_agy_messages_integration(tmp_path: Path) -> None:
    """测试 extract_agy_messages 综合读取。"""
    home = tmp_path
    sid = "conv_123"
    tr_path = home / ".gemini" / "antigravity-acp" / "brain" / sid / ".system_generated" / "logs" / "transcript.jsonl"
    tr_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>测试问题</USER_REQUEST>"},
        {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "测试回答"},
    ]
    tr_path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    msgs = extract_agy_messages(sid, home=home)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].parts[0].text == "测试问题"
    assert msgs[1].role == "assistant"
    assert msgs[1].parts[0].text == "测试回答"

    # 未找到的 sid 返回空列表
    assert extract_agy_messages("unknown_sid", home=home) == []
