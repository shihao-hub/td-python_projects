"""多源 Agent 会话 Skill 调用提取器与存在性审计。

基于会话原生历史轨迹（transcript.jsonl / jsonl / sqlite parts）只读逆向提取：
- 规范特征：tool_calls / tool_use / function_call 读取 .../skills/<skill_name>/SKILL.md
- 触发特征：用户输入中的显式命令唤起（如 /BuiltinSlashCommandName.<CMD> <name>, /<name>, <name>：）
- 审计特征：探测当前磁盘上该 skill 是否还存在，支持“旧会话 skill 消失了亦可如实展示”。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# 正则：匹配任意路径中的 skills/<name>/SKILL.md 或 skills/<name>/...
# 正反斜杠均兼容，兼容 json.dumps 转义的连续反斜杠，不区分大小写
SKILL_MD_PATTERN = re.compile(
    r"""(?:[\\/]+|\b)skills[\\/]+(?P<name>[a-zA-Z0-9_\-]+)[\\/]+SKILL\.md""",
    re.IGNORECASE,
)

# 兜底正则：匹配直接包含在 skills 目录下的子目录名
SKILL_DIR_PATTERN = re.compile(
    r"""(?:[\\/]+|\b)skills[\\/]+(?P<name>[a-zA-Z0-9_\-]+)(?:[\\/]+|["'\s]|$)""",
    re.IGNORECASE,
)

# 用户输入前缀触发命令正则：必须以 /BuiltinSlashCommandName, 行首/非词字符后的 / 或 # 唤起，严格匹配独立技能名
SLASH_CMD_PATTERN = re.compile(
    r"""(?:/BuiltinSlashCommandName\.\w+\s+|(?<![\w/])/(?=[a-zA-Z0-9_\-])|(?<!\w)#\s*)(?P<name>sh-[a-zA-Z0-9_\-]+|[a-zA-Z0-9_\-]+-skills?|anatomy(?:-curator)?|archify|book-[a-zA-Z0-9_\-]+|first-principles|find-skills|browser-skill|skill-creator)\b""",
    re.IGNORECASE,
)

COLON_PREFIX_PATTERN = re.compile(
    r"""^(?P<name>sh-[a-zA-Z0-9_\-]+|[a-zA-Z0-9_\-]+-skills?|anatomy(?:-curator)?|archify|book-[a-zA-Z0-9_\-]+|first-principles|find-skills|browser-skill|skill-creator)[：:]""",
    re.IGNORECASE,
)


@dataclass
class SkillInvocation:
    """单次 Skill 调用/激活证据记录。"""

    name: str
    agent_id: str
    session_id: str
    trigger_type: str  # "skill_doc_read" | "user_command" | "tool_call"
    detail: str
    invoked_at: str | None = None
    exists_on_disk: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_known_skills_dirs(home: Path | None = None) -> list[Path]:
    """获取系统中已知的技能库根目录候选列表。"""
    h = home or Path.home()
    return [
        h / ".gemini" / "config" / "skills",
        h / ".claude" / "skills",
        h / ".codex" / "skills",
        h / ".config" / "skills",
        h / ".opencode" / "skills",
    ]


def is_skill_on_disk(name: str, home: Path | None = None) -> bool:
    """检查当前磁盘上该 skill 是否仍然存在（即是否已被删除/消失）。"""
    if not name:
        return False
    clean_name = name.strip().lower()
    for base in get_known_skills_dirs(home):
        if not base.is_dir():
            continue
        try:
            for entry in os.scandir(base):
                if entry.is_dir() and entry.name.lower() == clean_name:
                    return True
        except OSError:
            continue
    return False


def _extract_from_text(text: str) -> list[tuple[str, str, str]]:
    """从文本中按多种规则提取 (skill_name, trigger_type, detail)。"""
    results: list[tuple[str, str, str]] = []
    if not text:
        return results

    for m in SKILL_MD_PATTERN.finditer(text):
        name = m.group("name")
        results.append((name, "skill_doc_read", m.group(0)))

    for m in SLASH_CMD_PATTERN.finditer(text):
        name = m.group("name")
        results.append((name, "user_command", m.group(0)))

    # 提取首个有效非 XML 标签行进行冒号前缀匹配
    content_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("<")
    ]
    if content_lines:
        first_line = content_lines[0]
        m_col = COLON_PREFIX_PATTERN.match(first_line)
        if m_col:
            results.append((m_col.group("name"), "user_command", first_line[:80]))

    return results


def extract_skills_from_antigravity(path: Path, session_id: str = "") -> list[SkillInvocation]:
    """从 Antigravity transcript.jsonl 中提取全部 skill 调用。"""
    invocations: list[SkillInvocation] = []
    if not path.is_file():
        return invocations

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

            ts = obj.get("created_at")
            ts_str = str(ts) if ts is not None else None
            t = obj.get("type")

            tool_calls = obj.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    tc_name = str(tc.get("name", ""))
                    args = tc.get("args") or {}
                    args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args)

                    for m in SKILL_MD_PATTERN.finditer(args_str):
                        s_name = m.group("name")
                        invocations.append(
                            SkillInvocation(
                                name=s_name,
                                agent_id="antigravity",
                                session_id=session_id,
                                trigger_type="skill_doc_read",
                                detail=f"{tc_name}({m.group(0)})",
                                invoked_at=ts_str,
                            )
                        )

            if t == "USER_INPUT":
                raw = obj.get("content", "")
                if isinstance(raw, str):
                    for s_name, trig, det in _extract_from_text(raw):
                        invocations.append(
                            SkillInvocation(
                                name=s_name,
                                agent_id="antigravity",
                                session_id=session_id,
                                trigger_type=trig,
                                detail=det,
                                invoked_at=ts_str,
                            )
                        )

    return _post_process_invocations(invocations)


def extract_skills_from_claude(path: Path, session_id: str = "") -> list[SkillInvocation]:
    """从 Claude Code JSONL 中提取 skill 调用。"""
    invocations: list[SkillInvocation] = []
    if not path.is_file():
        return invocations

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

            ts_str = obj.get("timestamp")
            t = obj.get("type")

            if t == "user":
                msg = obj.get("message", {})
                content = msg.get("content") if isinstance(msg, dict) else None
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(
                        p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                    )
                for s_name, trig, det in _extract_from_text(text):
                    invocations.append(
                        SkillInvocation(
                            name=s_name,
                            agent_id="claude-code",
                            session_id=session_id,
                            trigger_type=trig,
                            detail=det,
                            invoked_at=ts_str,
                        )
                    )
            elif t == "assistant":
                msg = obj.get("message", {})
                content = msg.get("content", []) if isinstance(msg, dict) else []
                if isinstance(content, list):
                    for p in content:
                        if isinstance(p, dict) and p.get("type") == "tool_use":
                            tool_name = str(p.get("name", "tool"))
                            inp = p.get("input") or {}
                            inp_str = json.dumps(inp, ensure_ascii=False) if isinstance(inp, (dict, list)) else str(inp)
                            for m in SKILL_MD_PATTERN.finditer(inp_str):
                                invocations.append(
                                    SkillInvocation(
                                        name=m.group("name"),
                                        agent_id="claude-code",
                                        session_id=session_id,
                                        trigger_type="skill_doc_read",
                                        detail=f"{tool_name}({m.group(0)})",
                                        invoked_at=ts_str,
                                    )
                                )

    return _post_process_invocations(invocations)


def extract_skills_from_codex(path: Path, session_id: str = "") -> list[SkillInvocation]:
    """从 Codex session JSONL 中提取 skill 调用。"""
    invocations: list[SkillInvocation] = []
    if not path.is_file():
        return invocations

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

            ts_str = obj.get("timestamp")
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue

            pt = payload.get("type")
            if pt == "message" and payload.get("role") == "user":
                content = payload.get("content", "")
                text = content if isinstance(content, str) else str(content)
                for s_name, trig, det in _extract_from_text(text):
                    invocations.append(
                        SkillInvocation(
                            name=s_name,
                            agent_id="codex",
                            session_id=session_id,
                            trigger_type=trig,
                            detail=det,
                            invoked_at=ts_str,
                        )
                    )
            elif pt == "function_call":
                t_name = str(payload.get("name", ""))
                args_str = str(payload.get("arguments", ""))
                for m in SKILL_MD_PATTERN.finditer(args_str):
                    invocations.append(
                        SkillInvocation(
                            name=m.group("name"),
                            agent_id="codex",
                            session_id=session_id,
                            trigger_type="skill_doc_read",
                            detail=f"{t_name}({m.group(0)})",
                            invoked_at=ts_str,
                        )
                    )

    return _post_process_invocations(invocations)


def extract_skills_from_pi(path: Path, session_id: str = "") -> list[SkillInvocation]:
    """从 Pi JSONL 中提取 skill 调用。"""
    invocations: list[SkillInvocation] = []
    if not path.is_file():
        return invocations

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

            ts_str = str(obj.get("timestamp", ""))
            t = obj.get("type")

            if t == "message":
                msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
                role = msg.get("role")
                content = msg.get("content")
                if role == "user" and isinstance(content, str):
                    for s_name, trig, det in _extract_from_text(content):
                        invocations.append(
                            SkillInvocation(
                                name=s_name,
                                agent_id="pi",
                                session_id=session_id,
                                trigger_type=trig,
                                detail=det,
                                invoked_at=ts_str,
                            )
                        )
                elif role == "assistant" and isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "toolCall":
                            name = str(part.get("name", "tool"))
                            args = part.get("arguments", {})
                            args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args)
                            for m in SKILL_MD_PATTERN.finditer(args_str):
                                invocations.append(
                                    SkillInvocation(
                                        name=m.group("name"),
                                        agent_id="pi",
                                        session_id=session_id,
                                        trigger_type="skill_doc_read",
                                        detail=f"{name}({m.group(0)})",
                                        invoked_at=ts_str,
                                    )
                                )

    return _post_process_invocations(invocations)


def extract_skills_from_opencode_parts(parts: list[dict], session_id: str = "") -> list[SkillInvocation]:
    """从 OpenCode 的 part 记录中提取 skill 调用。"""
    invocations: list[SkillInvocation] = []
    for p in parts:
        ptype = p.get("type", "")
        text = str(p.get("text") or "")
        data_str = json.dumps(p.get("data") or {}, ensure_ascii=False)
        ts_str = p.get("created_at")

        combined = f"{text} {data_str}"
        for m in SKILL_MD_PATTERN.finditer(combined):
            invocations.append(
                SkillInvocation(
                    name=m.group("name"),
                    agent_id="opencode",
                    session_id=session_id,
                    trigger_type="skill_doc_read",
                    detail=m.group(0),
                    invoked_at=ts_str,
                )
            )
        if ptype in ("text", "prompt"):
            for s_name, trig, det in _extract_from_text(text):
                invocations.append(
                    SkillInvocation(
                        name=s_name,
                        agent_id="opencode",
                        session_id=session_id,
                        trigger_type=trig,
                        detail=det,
                        invoked_at=ts_str,
                    )
                )

    return _post_process_invocations(invocations)


def _post_process_invocations(invocations: list[SkillInvocation]) -> list[SkillInvocation]:
    for inv in invocations:
        inv.exists_on_disk = is_skill_on_disk(inv.name)
    return invocations


def dedupe_skill_names(invocations: list[SkillInvocation]) -> list[str]:
    seen = set()
    result = []
    for inv in invocations:
        k = inv.name.lower()
        if k not in seen:
            seen.add(k)
            result.append(inv.name)
    return result


def quick_extract_skills_from_file(source: str, file_path: Path) -> list[str]:
    """快速提取单个会话文件中的 skills 列表（返回去重后的名称列表）。

    直接复用对应源的标准 extractor，确保与 extract_session_skills 保持 100% 严格一致的判定标准。
    """
    if not file_path.is_file():
        return []

    norm_src = (source or "").strip().lower()
    if "antigravity" in norm_src:
        invs = extract_skills_from_antigravity(file_path)
    elif "claude" in norm_src:
        invs = extract_skills_from_claude(file_path)
    elif "codex" in norm_src:
        invs = extract_skills_from_codex(file_path)
    elif "pi" in norm_src:
        invs = extract_skills_from_pi(file_path)
    else:
        invs = extract_skills_from_antigravity(file_path)

    return dedupe_skill_names(invs)


def extract_session_skills(
    source: str,
    session_id: str,
    *,
    home: Path | None = None,
    opencode_db: Path | None = None,
) -> tuple[list[str], list[SkillInvocation]]:
    """提取单个会话被调用的全部 skills 列表与详细调用记录。"""
    from .agent_paths import locate_session_files, FILE_SOURCES

    norm_src = (source or "").strip().lower()
    if "antigravity" in norm_src:
        norm_src = "antigravity"
    elif "claude" in norm_src:
        norm_src = "claude-code"
    elif "codex" in norm_src:
        norm_src = "codex"
    elif "pi" in norm_src:
        norm_src = "pi"
    elif "opencode" in norm_src:
        norm_src = "opencode"

    invocations: list[SkillInvocation] = []

    if norm_src == "opencode":
        from .snapshot import default_opencode_db_path, resolve_db_path
        target_db = resolve_db_path(opencode_db, default_opencode_db_path(), filename="opencode.db")
        if target_db.exists():
            import sqlite3
            with sqlite3.connect(f"file:{target_db}?mode=ro", uri=True) as con:
                con.row_factory = sqlite3.Row
                parts = []
                for r in con.execute(
                    "SELECT id, time_created, data FROM part WHERE session_id = ? ORDER BY time_created",
                    (session_id,),
                ):
                    try:
                        d = json.loads(r["data"]) if r["data"] else {}
                    except json.JSONDecodeError:
                        d = {}
                    parts.append({
                        "id": r["id"],
                        "type": d.get("type", ""),
                        "text": d.get("text", ""),
                        "data": d,
                        "created_at": str(r["time_created"]),
                    })
                invocations = extract_skills_from_opencode_parts(parts, session_id=session_id)
        return dedupe_skill_names(invocations), invocations

    sources_to_try = [norm_src] if norm_src in FILE_SOURCES else list(FILE_SOURCES)
    for src in sources_to_try:
        try:
            files_map = locate_session_files(src, [session_id], home=home)
            files = files_map.get(session_id, [])
            for f in files:
                if src == "antigravity":
                    if f.name == "transcript.jsonl":
                        invocations.extend(extract_skills_from_antigravity(f, session_id=session_id))
                elif src == "claude-code":
                    if f.suffix == ".jsonl":
                        invocations.extend(extract_skills_from_claude(f, session_id=session_id))
                elif src == "codex":
                    if f.suffix == ".jsonl":
                        invocations.extend(extract_skills_from_codex(f, session_id=session_id))
                elif src == "pi":
                    if f.suffix == ".jsonl":
                        invocations.extend(extract_skills_from_pi(f, session_id=session_id))
            if invocations:
                break
        except Exception:
            continue

    invocations.sort(key=lambda x: x.invoked_at or "")
    return dedupe_skill_names(invocations), invocations
