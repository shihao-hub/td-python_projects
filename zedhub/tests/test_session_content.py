from __future__ import annotations

import json
from pathlib import Path
import pytest

from zedhub.core.errors import NotFoundError, SourceNotSupportedError
from zedhub.core.message_extractor import (
    extract_claude_messages,
    extract_codex_messages,
    extract_pi_messages,
)
from zedhub.core.sources import Capability, get_source


def test_extract_claude_messages_filters_tools_and_thinking(tmp_path: Path) -> None:
    session_file = tmp_path / "claude_test.jsonl"
    lines = [
        # user prompt
        {"type": "user", "message": {"role": "user", "content": "Hello Claude"}, "timestamp": "2026-04-01T10:00:00Z"},
        # assistant with thinking and tool call
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Let me think about it..."},
                    {"type": "text", "text": "I will run a command for you."},
                    {"type": "tool_use", "id": "t1", "name": "run_command", "input": {"cmd": "ls"}},
                ],
            },
            "timestamp": "2026-04-01T10:00:05Z",
        },
        # tool result returned as type: user (must be filtered out!)
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "file1.txt file2.txt"}
                ],
            },
            "timestamp": "2026-04-01T10:00:10Z",
        },
        # assistant final response
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "All done!",
            },
            "timestamp": "2026-04-01T10:00:15Z",
        },
    ]
    with session_file.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")

    msgs = extract_claude_messages(session_file)
    assert len(msgs) == 3
    assert msgs[0].role == "user"
    assert msgs[0].parts[0].text == "Hello Claude"

    assert msgs[1].role == "assistant"
    assert msgs[1].parts[0].text == "I will run a command for you."

    assert msgs[2].role == "assistant"
    assert msgs[2].parts[0].text == "All done!"


def test_extract_codex_messages_filters_reasoning_and_calls(tmp_path: Path) -> None:
    session_file = tmp_path / "codex_test.jsonl"
    lines = [
        # session meta
        {"type": "session_meta", "payload": {"id": "test-session"}},
        # user prompt
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Fix this bug"}],
            },
        },
        # assistant reasoning
        {
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "summary": [{"type": "text", "text": "Analyzing the traceback..."}],
            },
        },
        # assistant function_call
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "edit_file",
                "arguments": "{}",
            },
        },
        # assistant reply
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "The bug is fixed."}],
            },
        },
    ]
    with session_file.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")

    msgs = extract_codex_messages(session_file)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].parts[0].text == "Fix this bug"

    assert msgs[1].role == "assistant"
    assert msgs[1].parts[0].text == "The bug is fixed."


def test_extract_pi_messages_filters_thinking_and_tools(tmp_path: Path) -> None:
    session_file = tmp_path / "pi_test.jsonl"
    lines = [
        {"type": "session", "id": "pi-session-123"},
        {
            "type": "message",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Write a poem"}],
                "timestamp": 1740000000000,
            },
        },
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Let me ponder rhymes"},
                    {"type": "toolCall", "id": "t1", "name": "dictionary"},
                    {"type": "text", "text": "Roses are red, violets are blue."},
                ],
                "timestamp": 1740000005000,
            },
        },
    ]
    with session_file.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")

    msgs = extract_pi_messages(session_file)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].parts[0].text == "Write a poem"

    assert msgs[1].role == "assistant"
    assert msgs[1].parts[0].text == "Roses are red, violets are blue."


def test_source_capabilities_and_aliases() -> None:
    claude = get_source("claude-code")
    assert Capability.CONTENT in claude.info.capabilities

    codex = get_source("codex")
    assert Capability.CONTENT in codex.info.capabilities

    pi = get_source("pi")
    assert Capability.CONTENT in pi.info.capabilities

    agy = get_source("antigravity")
    assert Capability.CONTENT not in agy.info.capabilities

    # 别名映射
    assert get_source("claude-acp") is claude
    assert get_source("codex-acp") is codex
    assert get_source("pi-acp") is pi


def test_antigravity_get_content_rejects() -> None:
    agy = get_source("antigravity")
    with pytest.raises(SourceNotSupportedError):
        agy.get_content("any-id")
