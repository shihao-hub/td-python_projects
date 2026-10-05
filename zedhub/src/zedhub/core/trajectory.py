"""多源轨迹解析：统一 TimelineEvent（/ui 三段式渲染用）。

- claude-code jsonl：跳过 mode/permission-mode/atis-latch；user 行取首条
  真人文本，assistant 行 content 数组中 text→thinking/text 输出、tool_use→
  tool_call，tool_result 行→tool_result；未知 type 进 collapsed 原始 JSON。
- codex jsonl：session_meta 记录 cwd/sid；response_item 中 message→按 role
  分 user/assistant，function_call→tool_call，function_call_output→
  tool_result，reasoning→thinking；其余 event_msg/response_item 进 raw。
- opencode sqlite：只读开库取 session/message/part 投影（本模块不写库）。
- antigravity 双源 sqlite（ACP 与桌面版同形）：steps 按 idx 序，可读列
  （metadata/render_info/task_details/error_details）尝试 utf-8/zlib 解码，
  step_payload 解不出则降级占位（step_type+长度），永不抛错丢行。
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path


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
                # summary/file-history 等未知行：collapsed 原始 JSON，不丢
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


def _try_decode_blob(blob: bytes) -> str | None:
    for candidate in (blob,):
        try:
            return candidate.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            pass
    try:
        return zlib.decompress(blob).decode("utf-8", errors="replace")
    except Exception:
        return None


def parse_antigravity_db(path: Path, *, limit: int = 2000) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT idx, step_type, status, metadata, render_info, task_details,"
            " error_details, step_payload FROM steps ORDER BY idx LIMIT ?", (limit,)).fetchall()
        for idx, step_type, status, metadata, render_info, task_details, err, payload in rows:
            text = ""
            for blob in (render_info, task_details, metadata):
                if isinstance(blob, bytes):
                    decoded = _try_decode_blob(blob)
                    if decoded and decoded.strip():
                        text = decoded[:8000]
                        break
            if not text and isinstance(payload, bytes) and payload:
                decoded = _try_decode_blob(payload)
                text = decoded[:8000] if decoded and decoded.strip() else (
                    f"<binary step_payload {len(payload)} bytes, step_type={step_type}>")
            role = "error" if err else ("tool_call" if step_type not in (0, 1) else "assistant")
            events.append(TimelineEvent(role=role, name=f"step#{idx}",
                status=str(status), text=text or f"<step {idx} type={step_type}>"))
    finally:
        con.close()
    return events


def load_trajectory(*, source: str, files: list[Path], limit: int = 2000,
                    db: Path | None = None) -> dict:
    """按源分派解析；files 为 locate 后的白名单内文件（opencode 时为 [session_id]）。

    db 仅 opencode 用：显式库路径（daemon 的 --opencode-db / OPENCODE_DATA）。
    """
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
            if f.suffix == ".db":
                used = str(f)
                events = parse_antigravity_db(f, limit=limit)
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
