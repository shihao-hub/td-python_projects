import json
import pytest
from pathlib import Path
from zedhub.core.skill_extractor import (
    extract_skills_from_antigravity,
    extract_skills_from_claude,
    extract_skills_from_codex,
    extract_skills_from_pi,
    extract_skills_from_opencode_parts,
    quick_extract_skills_from_file,
    dedupe_skill_names,
    is_skill_on_disk,
)

def test_extract_skills_from_antigravity(tmp_path):
    tr = tmp_path / "transcript.jsonl"
    lines = [
        {"type": "USER_INPUT", "created_at": "2026-10-28T10:00:00Z", "content": "<USER_REQUEST>/BuiltinSlashCommandName.PLAN sh-user-skills：帮我做个规划</USER_REQUEST>"},
        {
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-28T10:00:05Z",
            "tool_calls": [
                {
                    "name": "view_file",
                    "args": {"AbsolutePath": "C:\\Users\\29580\\.gemini\\config\\skills\\anatomy\\SKILL.md"}
                },
                {
                    "name": "run_command",
                    "args": {"CommandLine": "dir"}
                }
            ]
        },
        # 即使某个旧 skill 在本机已经被删除，这里也能解析出来
        {
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-28T10:01:00Z",
            "tool_calls": [
                {
                    "name": "view_file",
                    "args": {"AbsolutePath": "/deprecated/path/skills/legacy-deleted-skill/SKILL.md"}
                }
            ]
        }
    ]
    with tr.open("w", encoding="utf-8") as f:
        for l in lines:
            f.write(json.dumps(l) + "\n")

    invocations = extract_skills_from_antigravity(tr, session_id="test_sess_1")
    assert len(invocations) == 3

    names = dedupe_skill_names(invocations)
    assert names == ["sh-user-skills", "anatomy", "legacy-deleted-skill"]

    # legacy-deleted-skill 在磁盘上不存在
    legacy_inv = [inv for inv in invocations if inv.name == "legacy-deleted-skill"][0]
    assert legacy_inv.exists_on_disk is False

    # 快速抓取与详情提取完全对齐
    quick_names = quick_extract_skills_from_file("antigravity", tr)
    assert quick_names == names


def test_no_phantom_skills_from_assistant_thinking_or_diffs(tmp_path):
    """测试模型思考、代码 diff、工具输出中偶然提及的技能名或路径不会被误判为调用。"""
    tr = tmp_path / "transcript_with_phantoms.jsonl"
    lines = [
        {
            "type": "USER_INPUT",
            "created_at": "2026-10-28T10:00:00Z",
            "content": "<USER_REQUEST>帮我了解一下 deepseek 接口并编写一个 sh-cmd-desktop-dual-channel 的 demo</USER_REQUEST>"
        },
        {
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-28T10:00:05Z",
            "tool_calls": [
                {
                    "name": "view_file",
                    "args": {"AbsolutePath": "C:/skills/sh-user-skills/SKILL.md"}
                }
            ]
        },
        # 工具输出或模型思考中包含了带斜杠的路径 /anatomy，或者 markdown 标题 # sh-desktop-dual-channel
        {
            "type": "AGENCY_TOOL_CALL",
            "created_at": "2026-10-28T10:00:10Z",
            "content": "Available skills: /anatomy (C:/skills/anatomy/SKILL.md), # sh-desktop-dual-channel"
        },
        {
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-28T10:00:15Z",
            "content": "我正在思考是否需要使用 /anatomy 技能..."
        }
    ]
    with tr.open("w", encoding="utf-8") as f:
        for l in lines:
            f.write(json.dumps(l) + "\n")

    invs = extract_skills_from_antigravity(tr, session_id="phantom_test")
    names = dedupe_skill_names(invs)
    quick_names = quick_extract_skills_from_file("antigravity", tr)

    # 仅能识别出真实 view_file 触发的 sh-user-skills，绝对不能包含 phantom 的 anatomy 或 sh-desktop-dual-channel
    assert names == ["sh-user-skills"]
    assert quick_names == ["sh-user-skills"]
    assert len(invs) == 1


def test_extract_skills_from_claude(tmp_path):
    f = tmp_path / "claude.jsonl"
    lines = [
        {
            "type": "user",
            "timestamp": "2026-10-28T11:00:00Z",
            "message": {"content": "sh-oop-refactor：请重构这段代码"}
        },
        {
            "type": "assistant",
            "timestamp": "2026-10-28T11:00:02Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "ReadFile",
                        "input": {"file_path": "/home/user/.claude/skills/sh-monorepo-commit-push/SKILL.md"}
                    }
                ]
            }
        }
    ]
    with f.open("w", encoding="utf-8") as fh:
        for l in lines:
            fh.write(json.dumps(l) + "\n")

    invocations = extract_skills_from_claude(f, session_id="claude_s1")
    names = dedupe_skill_names(invocations)
    assert names == ["sh-oop-refactor", "sh-monorepo-commit-push"]


def test_extract_skills_from_opencode_parts():
    parts = [
        {
            "type": "tool",
            "data": {"tool": "view", "path": "D:/skills/first-principles/SKILL.md"},
            "created_at": "2026-10-28T12:00:00Z"
        }
    ]
    invocations = extract_skills_from_opencode_parts(parts, session_id="oc_s1")
    names = dedupe_skill_names(invocations)
    assert names == ["first-principles"]
