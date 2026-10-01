"""三 agent 源（claude-code/codex/antigravity）会话元数据扫描（sessions link）。

与 agent_paths.py 的整文件定位不同：本模块做**浅层元数据解析**——每会话
只读头部若干行与尾部残段，提取目录锚点 / 标题 / 时间戳，供补登
（sessions link）构造 Zed sidebar_threads 行；不解析会话正文。

数据形状（本机实测）：
- claude-code ``~/.claude/projects/*/<sid>.jsonl``：目录锚点取行内首个
  ``cwd`` 字段，标题取首条真人 user 消息（``type=user`` 且非 sidechain/
  meta），时间为行内 ISO ``Z`` 时间戳（首行建、末行更）；
- codex ``~/.codex/sessions/**/rollout-*-<sid>.jsonl``：首行
  ``session_meta.payload``（session_id/cwd/timestamp），标题取首条真人
  user 消息（``response_item`` role=user），时间为行内时间戳；
- antigravity ``~/.gemini/antigravity-acp/conversations/<sid>.meta``（JSON
  读 ``cwd``）+ 同名 ``.db``：无文本可提取（protobuf），title 留空，时间
  用 ``.db`` 文件 mtime 兜底；无 ``.meta`` 或缺 ``cwd`` 的会话 directory
  为空，由调用方跳过并计数（不猜测目录）。

环境注入块（``<environment_context>``、``# AGENTS.md instructions`` 等）
不是真人输入，跳过后再取标题；时间统一转 Zed ISO 格式（9 位小数 +
``+00:00``）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .agent_paths import FILE_SOURCES, agent_data_root
from .errors import SourceNotSupportedError

# 头部扫描行数上限：cwd/标题/首时间戳都在会话开头附近
_HEAD_LINES = 300
# 尾部时间戳窗口：末行 timestamp 通常在最后 64KB 内
_TAIL_WINDOW = 64 * 1024
# 标题长度上限（与 opencode link 预览口径一致量级）
_TITLE_MAX = 80
# 环境注入块前缀（codex/claude 的非真人首条 user 消息）
_INJECTED_PREFIXES = ("<", "# AGENTS.md")


@dataclass
class AgentSessionRecord:
    """补登用的最小会话元数据（时间已是 Zed ISO 格式）。"""

    session_id: str
    directory: str          # normpath；无法定位目录时为空串（调用方跳过计数）
    title: str
    time_created: str
    time_updated: str


def iso_z_to_zed_ts(raw: str) -> str | None:
    """行内 ISO 时间戳（``...Z`` 或带偏移）→ Zed UTC ISO（9 位小数 + +00:00）。

    无法解析返回 None（调用方以 mtime 兜底或保持空），不抛错——单个残段
    行不应让整源扫描失败。
    """
    text = raw.strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    dt = dt.astimezone(timezone.utc)
    ns = dt.microsecond * 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ns:09d}+00:00"


def mtime_ns_to_zed_ts(ns: int) -> str:
    """文件 mtime（纳秒）→ Zed UTC ISO。"""
    sec, nano = divmod(int(ns), 1_000_000_000)
    base = datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nano:09d}+00:00"


def _clean_title(text: str) -> str | None:
    """压平换行并截断；环境注入块返回 None（继续找下一条真人消息）。"""
    text = " ".join(text.split())
    if not text:
        return None
    if text.startswith(_INJECTED_PREFIXES):
        return None
    return text[:_TITLE_MAX]


def _claude_user_text(message: object) -> str | None:
    """claude-code user 行的 message.content（str 或 text part 数组）→ 文本。"""
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return _clean_title(content)
    if isinstance(content, list):
        text = " ".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
        return _clean_title(text)
    return None


def _codex_user_text(payload: dict) -> str | None:
    """codex response_item user 消息的 content 数组 → 文本。"""
    text = " ".join(
        c.get("text", "")
        for c in payload.get("content", [])
        if isinstance(c, dict) and c.get("type") in ("input_text", "text")
    )
    return _clean_title(text)


def _tail_timestamp(f: Path) -> str | None:
    """文件尾部残段取末行完整 JSON 的 timestamp（截断行跳过）。"""
    size = f.stat().st_size
    with f.open("rb") as fh:
        fh.seek(max(0, size - _TAIL_WINDOW))
        chunk = fh.read().decode("utf-8", errors="replace")
    for line in reversed(chunk.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("timestamp"), str):
            return obj["timestamp"]
    return None


def _dedupe_records(records: list[AgentSessionRecord]) -> list[AgentSessionRecord]:
    """同 session_id 多文件（codex resume 沿用原 sid、claude 目录改名前后）
    合并为一条：标题取首个非空（文件名序即时间序，最早的真人意图），
    created 取更早、updated 取更晚、directory 取非空。
    """
    merged: dict[str, AgentSessionRecord] = {}
    for r in records:
        prev = merged.get(r.session_id)
        if prev is None:
            merged[r.session_id] = AgentSessionRecord(
                session_id=r.session_id, directory=r.directory, title=r.title,
                time_created=r.time_created, time_updated=r.time_updated,
            )
            continue
        prev.directory = prev.directory or r.directory
        prev.title = prev.title or r.title
        prev.time_created = min(prev.time_created, r.time_created)
        prev.time_updated = max(prev.time_updated, r.time_updated)
    return [merged[sid] for sid in sorted(merged)]


def _scan_claude(home: Path | None) -> list[AgentSessionRecord]:
    base = agent_data_root("claude-code", home=home) / "projects"
    records: list[AgentSessionRecord] = []
    if not base.is_dir():
        return records
    for f in sorted(base.glob("*/*.jsonl")):
        sid = f.stem
        first_ts: str | None = None
        cwd: str | None = None
        title: str | None = None
        with f.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= _HEAD_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if first_ts is None and isinstance(obj.get("timestamp"), str):
                    first_ts = obj["timestamp"]
                if cwd is None and isinstance(obj.get("cwd"), str) and obj["cwd"]:
                    cwd = obj["cwd"]
                if (
                    title is None
                    and obj.get("type") == "user"
                    and not obj.get("isSidechain")
                    and not obj.get("isMeta")
                ):
                    title = _claude_user_text(obj.get("message"))
                if first_ts and cwd and title:
                    break
        created = iso_z_to_zed_ts(first_ts or "")
        updated = iso_z_to_zed_ts(_tail_timestamp(f) or "") or created
        if created is None:
            # 头部无时间戳（罕见）：mtime 兜底，保记录不丢
            created = mtime_ns_to_zed_ts(f.stat().st_mtime_ns)
            updated = updated or created
        records.append(
            AgentSessionRecord(
                session_id=sid,
                directory=os.path.normpath(cwd) if cwd else "",
                title=title or "",
                time_created=created,
                time_updated=updated or created,
            )
        )
    return _dedupe_records(records)


def _scan_codex(home: Path | None) -> list[AgentSessionRecord]:
    base = agent_data_root("codex", home=home) / "sessions"
    records: list[AgentSessionRecord] = []
    if not base.is_dir():
        return records
    for f in sorted(base.rglob("*.jsonl")):
        sid: str | None = None
        first_ts: str | None = None
        cwd: str | None = None
        title: str | None = None
        with f.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= _HEAD_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                payload = obj.get("payload")
                payload = payload if isinstance(payload, dict) else {}
                if obj.get("type") == "session_meta":
                    sid = sid or payload.get("session_id") or payload.get("id")
                    cwd = cwd or payload.get("cwd")
                    first_ts = first_ts or obj.get("timestamp") or payload.get("timestamp")
                elif (
                    title is None
                    and obj.get("type") == "response_item"
                    and payload.get("type") == "message"
                    and payload.get("role") == "user"
                ):
                    title = _codex_user_text(payload)
                if sid and cwd and title:
                    break
        if not sid:
            # 首行残缺时从文件名尾段兜底（rollout-<ts>-<uuid>.jsonl）
            stem = f.stem
            if len(stem) >= 36 and stem[-37] == "-":
                sid = stem[-36:]
            else:
                continue  # 无法锚定 session_id 的文件不猜
        created = iso_z_to_zed_ts(first_ts or "")
        updated = iso_z_to_zed_ts(_tail_timestamp(f) or "") or created
        if created is None:
            created = mtime_ns_to_zed_ts(f.stat().st_mtime_ns)
            updated = updated or created
        records.append(
            AgentSessionRecord(
                session_id=sid,
                directory=os.path.normpath(cwd) if cwd else "",
                title=title or "",
                time_created=created,
                time_updated=updated or created,
            )
        )
    return _dedupe_records(records)


def _scan_antigravity(home: Path | None) -> list[AgentSessionRecord]:
    base = agent_data_root("antigravity", home=home) / "antigravity-acp" / "conversations"
    records: list[AgentSessionRecord] = []
    if not base.is_dir():
        return records
    # 以 .db（会话本体）为锚：缺 .meta 或缺 cwd 的记录 directory 留空，
    # 由调用方跳过并计数（no_directory），不猜测目录
    for db in sorted(base.glob("*.db")):
        sid = db.stem
        meta = base / f"{sid}.meta"
        cwd = None
        if meta.is_file():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            raw = data.get("cwd") if isinstance(data, dict) else None
            cwd = raw if isinstance(raw, str) and raw else None
        ts = mtime_ns_to_zed_ts(db.stat().st_mtime_ns)
        records.append(
            AgentSessionRecord(
                session_id=sid,
                directory=os.path.normpath(cwd) if cwd else "",
                title="",  # 对话本体是 protobuf（无公开 schema），标题留空
                time_created=ts,
                time_updated=ts,
            )
        )
    return records


def scan_agent_sessions(source: str, *, home: Path | None = None) -> list[AgentSessionRecord]:
    """扫描一个文件级源的全会话元数据（link 补登的数据面）。

    数据根不存在时安静返回空列表（如实按无会话报告）；未知 source 抛
    SourceNotSupportedError。directory 为空的记录原样返回，由调用方
    跳过并计数。
    """
    if source == "claude-code":
        return _scan_claude(home)
    if source == "codex":
        return _scan_codex(home)
    if source == "antigravity":
        return _scan_antigravity(home)
    raise SourceNotSupportedError(
        f"数据源未支持: {source}；link 已支持: opencode, " + ", ".join(FILE_SOURCES)
    )
