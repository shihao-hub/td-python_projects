"""多源轨迹解析：统一 TimelineEvent（/ui 三段式渲染用）。

- claude-code jsonl：跳过 mode/permission-mode/atis-latch；user 行取首条
  真人文本，assistant 行 content 数组中 text→thinking/text 输出、tool_use→
  tool_call，tool_result 行→tool_result；未知 type 进 collapsed 原始 JSON。
- codex jsonl：session_meta 记录 cwd/sid；response_item 中 message→按 role
  分 user/assistant，function_call→tool_call，function_call_output→
  tool_result，reasoning→thinking；其余 event_msg/response_item 进 raw。
- opencode sqlite：只读开库取 session/message/part 投影（本模块不写库）。
- pi jsonl：session/message/toolCall/toolResult 等事件流投影。
- antigravity jsonl：原生 transcript.jsonl（USER_INPUT/PLANNER_RESPONSE，
  包含 thinking、tool_calls、assistant 回复）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .agent_paths import FILE_SOURCES
from .agy_extractor import _extract_user_request


@dataclass
class TimelineEvent:
    role: str  # user|thinking|assistant|tool_call|tool_result|system|error|raw
    text: str = ""
    name: str = ""
    status: str = ""
    ts: str = ""
    raw: str = ""


def event_to_dict(e: TimelineEvent) -> dict:
    return asdict(e)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) for p in content
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
        )
    return ""


def _join_part_text(content) -> str:
    """codex content 数组 -> 拼接文本；None/非 list（如 null）安全返回空串。"""
    if not isinstance(content, list):
        return ""
    return " ".join(
        str(p.get("text", "")) for p in content
        if isinstance(p, dict) and p.get("text")
    )


def parse_claude_file(path: Path, *, limit: int = 2000) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
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
            if t in ("mode", "permission-mode", "atis-latch", "queue-operation"):
                continue
            ts = obj.get("timestamp", "") if isinstance(obj.get("timestamp"), str) else ""
            if t == "user":
                msg = obj.get("message", {})
                text = _text_of(msg.get("content") if isinstance(msg, dict) else None)
                if text:
                    events.append(TimelineEvent(role="user", text=text[:8000], ts=ts))
            elif t == "assistant":
                msg = obj.get("message", {})
                content = msg.get("content", []) if isinstance(msg, dict) else []
                thinks: list[str] = []
                for p in content if isinstance(content, list) else []:
                    if not isinstance(p, dict):
                        continue
                    if p.get("type") == "text" and p.get("text"):
                        thinks.append(str(p["text"]))
                    elif p.get("type") == "tool_use":
                        events.append(TimelineEvent(
                            role="tool_call", name=str(p.get("name", "tool")),
                            text=json.dumps(p.get("input", {}), ensure_ascii=False)[:4000], ts=ts))
                if thinks:
                    events.append(TimelineEvent(role="assistant", text="\n".join(thinks)[:8000], ts=ts))
            elif t == "tool_result":
                events.append(TimelineEvent(role="tool_result",
                    text=str(obj.get("toolUseResult", obj.get("content", "")))[:8000], ts=ts))
            else:
                events.append(TimelineEvent(role="raw", text=json.dumps(obj, ensure_ascii=False)[:2000], ts=ts))
            if len(events) >= limit:
                break
    return events


def parse_codex_file(path: Path, *, limit: int = 2000) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
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
            payload = obj.get("payload", {})
            payload = payload if isinstance(payload, dict) else {}
            ts = obj.get("timestamp", "") if isinstance(obj.get("timestamp"), str) else ""
            if t == "session_meta":
                cwd = payload.get("cwd", "")
                events.append(TimelineEvent(role="system", text=f"cwd: {cwd}", ts=ts))
            elif t == "response_item":
                pt = payload.get("type")
                if pt == "message":
                    text = _join_part_text(payload.get("content"))
                    role = "user" if payload.get("role") == "user" else "assistant"
                    if text:
                        events.append(TimelineEvent(role=role, text=text[:8000], ts=ts))
                elif pt == "function_call":
                    events.append(TimelineEvent(role="tool_call",
                        name=str(payload.get("name", "tool")),
                        text=str(payload.get("arguments", ""))[:4000], ts=ts))
                elif pt == "function_call_output":
                    events.append(TimelineEvent(role="tool_result",
                        text=str(payload.get("output", ""))[:8000], ts=ts))
                elif pt == "reasoning":
                    text = _join_part_text(payload.get("content"))
                    if text:
                        events.append(TimelineEvent(role="thinking", text=text[:8000], ts=ts))
                else:
                    events.append(TimelineEvent(role="raw",
                        text=json.dumps(payload, ensure_ascii=False)[:2000], ts=ts))
            elif t == "event_msg":
                inner = payload.get("type", t)
                if inner in ("task_started", "task_complete", "turn_aborted"):
                    events.append(TimelineEvent(role="system", text=inner, ts=ts))
            else:
                events.append(TimelineEvent(role="raw",
                    text=json.dumps(obj, ensure_ascii=False)[:2000], ts=ts))
            if len(events) >= limit:
                break
    return events


def parse_pi_file(path: Path, *, limit: int = 2000) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
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
            ts = obj.get("timestamp", "")
            ts = str(ts) if ts is not None else ""

            if t == "session":
                cwd = obj.get("cwd", "")
                events.append(TimelineEvent(role="system", text=f"cwd: {cwd}", ts=ts))
            elif t == "model_change":
                provider = obj.get("provider", "")
                model_id = obj.get("modelId", "")
                events.append(TimelineEvent(role="system", text=f"model: {provider}/{model_id}", ts=ts))
            elif t == "thinking_level_change":
                level = obj.get("thinkingLevel", "")
                events.append(TimelineEvent(role="system", text=f"thinking_level: {level}", ts=ts))
            elif t == "message":
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    msg = obj
                role = msg.get("role")
                content = msg.get("content")

                if role == "system":
                    text = _text_of(content)
                    if not text:
                        sections = msg.get("sections")
                        if isinstance(sections, dict):
                            text = "\n".join(f"{k}: {v}" for k, v in sections.items() if v)
                    if text:
                        events.append(TimelineEvent(role="system", text=text[:8000], ts=ts))
                elif role == "user":
                    text = _text_of(content)
                    if not text and isinstance(content, (dict, list)):
                        text = json.dumps(content, ensure_ascii=False)
                    if text:
                        events.append(TimelineEvent(role="user", text=text[:8000], ts=ts))
                elif role == "assistant":
                    if isinstance(content, list):
                        for part in content:
                            if not isinstance(part, dict):
                                continue
                            pt = part.get("type")
                            if pt == "thinking":
                                thinking_text = str(part.get("thinking", ""))
                                if thinking_text:
                                    events.append(TimelineEvent(role="thinking", text=thinking_text[:8000], ts=ts))
                            elif pt == "text":
                                asst_text = str(part.get("text", ""))
                                if asst_text:
                                    events.append(TimelineEvent(role="assistant", text=asst_text[:8000], ts=ts))
                            elif pt == "toolCall":
                                name = str(part.get("name", "tool"))
                                args = part.get("arguments", {})
                                args_text = json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args)
                                events.append(TimelineEvent(
                                    role="tool_call",
                                    name=name,
                                    text=args_text[:4000],
                                    ts=ts,
                                ))
                    elif isinstance(content, str) and content.strip():
                        events.append(TimelineEvent(role="assistant", text=content[:8000], ts=ts))
                elif role == "toolResult":
                    tool_name = str(msg.get("toolName", obj.get("toolName", "tool")))
                    text = _text_of(content)
                    if not text and isinstance(content, (dict, list)):
                        text = json.dumps(content, ensure_ascii=False)
                    events.append(TimelineEvent(
                        role="tool_result",
                        name=tool_name,
                        text=str(text)[:8000],
                        ts=ts,
                    ))
                else:
                    events.append(TimelineEvent(role="raw", text=json.dumps(obj, ensure_ascii=False)[:2000], ts=ts))
            else:
                events.append(TimelineEvent(role="raw", text=json.dumps(obj, ensure_ascii=False)[:2000], ts=ts))

            if len(events) >= limit:
                break
    return events


def parse_antigravity_transcript(path: Path, *, limit: int = 2000) -> list[TimelineEvent]:
    """Antigravity transcript.jsonl 原生解析：提取用户输入、思考过程、工具调用与助手回复。"""
    events: list[TimelineEvent] = []
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
            ts = obj.get("created_at", "")
            ts = str(ts) if ts is not None else ""

            if t == "USER_INPUT":
                raw = obj.get("content", "")
                text = _extract_user_request(raw)
                if text:
                    events.append(TimelineEvent(role="user", text=text[:8000], ts=ts))
            elif t == "PLANNER_RESPONSE":
                # 1. 思考链
                thinking = obj.get("thinking", "")
                if isinstance(thinking, str) and thinking.strip():
                    events.append(TimelineEvent(role="thinking", text=thinking[:8000], ts=ts))
                # 2. 工具调用列表
                tool_calls = obj.get("tool_calls", [])
                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            t_name = str(tc.get("name", "tool"))
                            args = tc.get("args", {})
                            args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args)
                            events.append(TimelineEvent(
                                role="tool_call",
                                name=t_name,
                                text=args_str[:4000],
                                ts=ts,
                            ))
                # 3. 助手正文回复
                content = obj.get("content", "")
                if isinstance(content, str) and content.strip():
                    events.append(TimelineEvent(role="assistant", text=content[:8000], ts=ts))
            elif t in ("SEARCH_WEB", "VIEW_FILE", "RUN_COMMAND", "CLIENT_VIEW_FILE", "CLIENT_EDIT_FILE"):
                # 工具执行结果回声
                status = obj.get("status", "")
                content = obj.get("content", "")
                if isinstance(content, str) and content.strip():
                    events.append(TimelineEvent(
                        role="tool_result",
                        name=t.lower(),
                        status=str(status),
                        text=content[:8000],
                        ts=ts,
                    ))
            else:
                # 其他类型进入 raw
                events.append(TimelineEvent(role="raw", text=json.dumps(obj, ensure_ascii=False)[:2000], ts=ts))

            if len(events) >= limit:
                break
    return events


def _file_uri_to_path(uri: str) -> str:
    """file:///D:/... URI → 本地路径（normcase）。"""
    from urllib.parse import unquote, urlparse
    try:
        parts = urlparse(uri)
    except ValueError:
        return ""
    if parts.scheme != "file":
        return ""
    text = unquote(parts.path)
    if len(text) > 2 and text[0] == "/" and text[2] == ":":
        text = text[1:]
    import os
    return os.path.normcase(os.path.normpath(text))


def _zed_iso_to_ts(raw: str) -> float | None:
    from datetime import datetime
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def resolve_by_directory(*, source: str, directory: str, near_ts: float = 0.0,
                         home: Path | None = None) -> tuple[list[Path], str, int]:
    """Zed 线程目录 → 源文件 sid 回退映射（trajectory.show miss 时用）。"""
    import os
    from .agent_paths import locate_session_files
    if not directory:
        return [], "", 0
    want = os.path.normcase(os.path.normpath(directory))
    scored: list[tuple[float, str]] = []  # (diff, sid)

    if source in ("antigravity", "antigravity-desktop"):
        from .agent_sessions import scan_agent_sessions
        records = scan_agent_sessions("antigravity", home=home)
        for r in records:
            if not r.directory or os.path.normcase(os.path.normpath(r.directory)) != want:
                continue
            ts = _zed_iso_to_ts(r.time_updated) or _zed_iso_to_ts(r.time_created) or 0.0
            scored.append((abs(ts - near_ts) if near_ts and ts else 0.0, r.session_id))
    elif source in FILE_SOURCES:
        from .agent_sessions import scan_agent_sessions
        records = scan_agent_sessions(source, home=home)
        for r in records:
            if not r.directory or os.path.normcase(os.path.normpath(r.directory)) != want:
                continue
            ts = _zed_iso_to_ts(r.time_updated) or _zed_iso_to_ts(r.time_created) or 0.0
            scored.append((abs(ts - near_ts) if near_ts and ts else 0.0, r.session_id))
    else:
        return [], "", 0
    if not scored:
        return [], "", 0
    scored.sort()
    if near_ts and scored[0][0] > 30 * 86400:
        return [], "", len(scored)
    sid = scored[0][1]
    return (locate_session_files("antigravity" if "antigravity" in source else source, [sid]).get(sid, []), sid, len(scored))


def load_trajectory(*, source: str, files: list[Path], limit: int = 2000,
                    db: Path | None = None) -> dict:
    """按源分派解析：files 为 locate 后的白名单内文件。"""
    events: list[TimelineEvent] = []
    used = ""
    if source == "claude-code":
        for f in files:
            if f.suffix == ".jsonl":
                used = str(f)
                events = parse_claude_file(f, limit=limit)
                break
    elif source == "codex":
        for f in files:
            if f.suffix == ".jsonl":
                used = str(f)
                events = parse_codex_file(f, limit=limit)
                break
    elif source in ("antigravity", "antigravity-desktop"):
        for f in files:
            if f.name.endswith(".jsonl") or f.suffix == ".jsonl":
                used = str(f)
                events = parse_antigravity_transcript(f, limit=limit)
                break
    elif source in ("pi", "pi-acp"):
        for f in files:
            if f.suffix == ".jsonl":
                used = str(f)
                events = parse_pi_file(f, limit=limit)
                break
    elif source == "opencode":
        from .opencode_repo import OpencodeDb
        from .snapshot import open_opencode_ro
        sid = str(files[0]) if files else ""
        with open_opencode_ro(db) as opened:
            oc = OpencodeDb(opened.con, db_path=opened.db_path, using_snapshot=opened.using_snapshot)
            content = oc.get_session_content(sid)
            sid = content.session.external_id
            for m in content.messages[:500]:
                texts = [p.text for p in m.parts if getattr(p, "type", "") == "text" and p.text]
                if texts:
                    events.append(TimelineEvent(
                        role="user" if m.role == "user" else "assistant",
                        text="\n".join(texts)[:8000], ts=str(m.created_at)))
                if len(events) >= limit:
                    break
        used = f"opencode:{sid}"
    return {"source": source, "file": used,
            "count": len(events), "events": [event_to_dict(e) for e in events]}
