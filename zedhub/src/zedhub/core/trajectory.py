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

from .agent_paths import FILE_SOURCES


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


def _pb_varint(b: bytes, i: int) -> tuple[int, int]:
    """raw protobuf varint（无第三方依赖，protoc 不可用环境也能解）。"""
    r = 0
    s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if not x & 0x80:
            return r, i


def _pb_strings(b: bytes, *, depth: int = 0, path: str = "",
                max_depth: int = 9) -> dict[str, list[str]]:
    """raw walk：只收集可读 UTF-8 字符串叶，按完整字段路径分组。

    无公开 .proto，用启发式字段映射（见 _agy_step_events）；解不出
    的分支直接跳过（不抛错），调用方保留降级占位。
    """
    out: dict[str, list[str]] = {}
    i = 0
    try:
        while i < len(b):
            tag, i = _pb_varint(b, i)
            fn, wt = tag >> 3, tag & 7
            p = f"{path}/{fn}"
            if wt == 0:
                _, i = _pb_varint(b, i)
            elif wt == 1:
                i += 8
            elif wt == 5:
                i += 4
            elif wt == 2:
                n, i = _pb_varint(b, i)
                chunk = b[i:i + n]
                i += n
                try:
                    t = chunk.decode("utf-8")
                except UnicodeDecodeError:
                    t = None
                if (
                    t is not None and len(t) >= 2
                    and all(32 <= ord(c) < 127 or c in "\n\t" for c in t)
                ):
                    out.setdefault(p, []).append(t)
                elif 8 <= n < len(b) and depth < max_depth:
                    for kp, v in _pb_strings(chunk, depth=depth + 1, path=p).items():
                        out.setdefault(kp, []).extend(v)
            else:
                break
    except IndexError:
        pass
    return out


def _agy_step_events(step_type: int, fields: dict[str, list[str]]) -> list[TimelineEvent]:
    """启发式映射（实测 ACP/桌面版同形）：user /20/3；工具调用 /20/7 与
    /5/4 回声 (1=id,2=name,3=argsJSON)；assistant 文本 /140/2/1；
    工具结果回声 /140/1 与 /140/2/6/2 下长文本。/5/24 配置子树跳过。"""
    events: list[TimelineEvent] = []
    seen_calls: set[tuple[str, str]] = set()

    def _texts(*paths: str) -> list[str]:
        got: list[str] = []
        for p in paths:
            for t in fields.get(p, []):
                if t.strip():
                    got.append(t)
        return got

    for t in _texts("/20/3"):
        if len(t) >= 4 and "/5/24" not in t:
            events.append(TimelineEvent(role="user", text=t[:8000]))
    for base in ("/20/7", "/5/4"):
        ids = fields.get(f"{base}/1", [])
        names = fields.get(f"{base}/2", [])
        args = fields.get(f"{base}/3", [])
        for k in range(max(len(ids), len(names), len(args))):
            name = names[k] if k < len(names) else ""
            arg = args[k] if k < len(args) else ""
            if not name and not arg:
                continue
            key = (name, arg[:200])
            if key in seen_calls:
                continue
            seen_calls.add(key)
            cid = ids[k] if k < len(ids) else ""
            events.append(TimelineEvent(
                role="tool_call", name=name or "tool",
                text=((cid + "\n" if cid else "") + arg)[:4000]))
    for t in _texts("/140/2/1"):
        if len(t) >= 8:
            events.append(TimelineEvent(role="assistant", text=t[:8000]))
    result_bits: list[str] = []
    for p, vals in fields.items():
        if p.startswith("/140/1/") or (
            p.startswith("/140/2/6/2/") and p not in ("/140/2/6/2/14/1",)
        ):
            for t in vals:
                if len(t) >= 8 and not t.startswith("file://") and "type.googleapis.com" not in t:
                    result_bits.append(t)
    if result_bits and not any(e.role == "assistant" for e in events):
        events.append(TimelineEvent(role="tool_result",
                                    text="\n".join(result_bits)[:8000]))
    _ = step_type
    return events


def parse_antigravity_db(path: Path, *, limit: int = 2000) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    prev_key: tuple[str, str] | None = None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT idx, step_type, status, metadata, render_info, task_details,"
            " error_details, step_payload FROM steps ORDER BY idx LIMIT ?", (limit,)).fetchall()
        for idx, step_type, status, metadata, render_info, task_details, err, payload in rows:
            if err:
                events.append(TimelineEvent(role="error", name=f"step#{idx}",
                                            status=str(status), text="step failed"))
                continue
            mapped = _agy_step_events(
                step_type, _pb_strings(payload) if isinstance(payload, bytes) else {})
            if mapped:
                for e in mapped:
                    if not e.name:
                        e.name = f"step#{idx}"
                    if not e.status:
                        e.status = str(status)
                    key = (e.role, (e.name or "") + (e.text or "")[:200])
                    if key == prev_key:
                        continue  # 相邻步的同一调用回声去重
                    prev_key = key
                    events.append(e)
                continue
            text = ""
            for blob in (render_info, task_details, metadata):
                if isinstance(blob, bytes):
                    decoded = _try_decode_blob(blob)
                    if decoded and decoded.strip():
                        text = decoded[:8000]
                        break
            if not text:
                text = (f"<undecoded step_payload {len(payload)} bytes, "
                        f"step_type={step_type}>") if isinstance(payload, bytes) else (
                            f"<step {idx} type={step_type}>")
            events.append(TimelineEvent(role="tool_call" if step_type not in (0, 1) else "assistant",
                                        name=f"step#{idx}", status=str(status), text=text))
    finally:
        con.close()
    return events


def _file_uri_to_path(uri: str) -> str:
    """trajectory_metadata_blob 里的 file:///D:/... URI → 本地路径（normcase）。"""
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
    """Zed 线程目录 → 源文件 sid 回退映射（trajectory.show miss 时用）。

    返回 (files, matched_sid, candidates)：目录归一化全等过滤（大小写
    不敏感），时间最接近者胜出；最优时间差超 30 天或目录为空视为无匹配。
    只读，不写库；多候选不断言唯一（取最优并上报候选数）。
    """
    import os
    from .agent_paths import locate_session_files
    if not directory:
        return [], "", 0
    want = os.path.normcase(os.path.normpath(directory))
    scored: list[tuple[float, str]] = []  # (diff, sid)

    if source == "antigravity-desktop":
        from .agent_paths import agent_data_root
        base = agent_data_root("antigravity", home=home) / ".." / "antigravity" / "conversations"
        cands = sorted(base.glob("*.db")) if base.is_dir() else []
        for db in cands:
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                try:
                    blob = con.execute(
                        "SELECT data FROM trajectory_metadata_blob WHERE id='main' LIMIT 1"
                    ).fetchone()
                finally:
                    con.close()
            except Exception:
                continue
            if not blob or not isinstance(blob[0], bytes):
                continue
            cwd = ""
            for vals in _pb_strings(blob[0]).values():
                for t in vals:
                    if t.startswith("file://"):
                        cwd = _file_uri_to_path(t)
                        break
                if cwd:
                    break
            if cwd != want:
                continue
            try:
                diff = abs(db.stat().st_mtime - near_ts) if near_ts else 0.0
            except OSError:
                continue
            scored.append((diff, db.stem))
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
    if source == "antigravity-desktop":
        from .agent_paths import agent_data_root
        f = (agent_data_root("antigravity", home=home) / ".." / "antigravity"
             / "conversations" / f"{sid}.db")
        return ([f] if f.is_file() else [], sid, len(scored))
    return (locate_session_files(source, [sid]).get(sid, []), sid, len(scored))


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
