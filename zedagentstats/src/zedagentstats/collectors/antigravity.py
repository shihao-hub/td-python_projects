"""antigravity-acp 采集器。

数据源（规划期已探明）：
- ``~/.gemini/antigravity-acp/conversations/<sid>.db``：每会话一个 SQLite，
  ``gen_metadata`` 表每次 LLM 生成一行，``data`` 列为无 .proto 的 protobuf blob。
  按字段号轻量遍历提取（全量 corpus 实测 0.04s，blackboxprotobuf 1.0.1 需 22s，已弃用）：
  - ``1.19`` → 模型名（明文字符串）
  - ``1.4``  → usage 子消息：f2=input、f3=output、f5=reasoning(thoughts)
  - f9/f10 语义未定（数值远小于主字段），保守忽略、不计入 total（待校准）
- ``<sid>.meta``：JSON，含 ``cwd``（项目目录）
- Zed ``sidebar_threads``（agent_id='antigravity-acp'）：标题/folder/时间戳

antigravity 无任何 cost 数据（订阅制），费用恒为 0。
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zedagentstats.collectors._zed import get_zed_threads
from zedagentstats.models import ModelUsage, SessionStats, TokenUsage

ZED_AGENT_ID = "antigravity-acp"
AGENT_NAME = "antigravity"


# --------------------------------------------------------------------------
# 无 schema protobuf 轻量遍历（仅按字段号提取，不构建完整消息树）
# --------------------------------------------------------------------------

def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """读取 varint，返回 (值, 新位置)。"""
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not b & 0x80:
            return result, pos
        shift += 7


def _parse_fields(buf: bytes) -> list[tuple[int, int, Any]]:
    """按 wire format 遍历一层字段，返回 (字段号, wire 类型, 值) 列表。"""
    out: list[tuple[int, int, Any]] = []
    pos = 0
    n = len(buf)
    while pos < n:
        try:
            key, pos = _read_varint(buf, pos)
            field, wt = key >> 3, key & 7
            if wt == 0:
                val, pos = _read_varint(buf, pos)
            elif wt == 2:
                ln, pos = _read_varint(buf, pos)
                val = buf[pos : pos + ln]
                pos += ln
            elif wt == 5:
                val = buf[pos : pos + 4]
                pos += 4
            elif wt == 1:
                val = buf[pos : pos + 8]
                pos += 8
            else:
                break
            out.append((field, wt, val))
        except Exception:
            break
    return out


def _field_bytes(fields: list[tuple[int, int, Any]], want: int) -> bytes | None:
    """取首个 wire type 2 (length-delimited) 的指定字段。"""
    for field, wt, val in fields:
        if field == want and wt == 2:
            return bytes(val)
    return None


def _field_int(fields: list[tuple[int, int, Any]], want: int) -> int:
    """取首个 varint 字段值（缺失返回 0）。"""
    for field, wt, val in fields:
        if field == want and wt == 0:
            return int(val)
    return 0


def _decode_generation(blob: bytes) -> tuple[TokenUsage, str] | None:
    """解码单条 gen_metadata：返回 (usage, model)，无法解析返回 None。"""
    root = _parse_fields(bytes(blob))
    gen = _field_bytes(root, 1)
    if gen is None:
        return None

    gen_fields = _parse_fields(gen)
    usage_raw = _field_bytes(gen_fields, 4)
    if usage_raw is None:
        # 无 usage 的生成（如失败请求）跳过
        return None

    model_raw = _field_bytes(gen_fields, 19)
    if model_raw is not None:
        model = model_raw.decode("utf-8", "replace") or "unknown"
    else:
        model = "unknown"

    usage_fields = _parse_fields(usage_raw)
    inp = _field_int(usage_fields, 2)
    out = _field_int(usage_fields, 3)
    reasoning = _field_int(usage_fields, 5)
    # f9/f10 语义未定，保守忽略不计入 total（README 注明待校准）
    usage = TokenUsage(
        input_tokens=inp,
        output_tokens=out,
        reasoning_tokens=reasoning,
        total_tokens=inp + out + reasoning,
    )
    return usage, model


def _parse_conversation_db(
    db_file: Path, snapshot_dir: Path
) -> tuple[TokenUsage, dict[str, ModelUsage], int]:
    """快照读取单个会话库并聚合 gen_metadata。

    Args:
        db_file: 会话库原路径
        snapshot_dir: 本次采集共用的快照目录（调用方统一创建与清理）

    Returns:
        (total_usage, model_usages, generations)
    """
    total = TokenUsage()
    model_usages: dict[str, ModelUsage] = {}
    gens = 0

    try:
        snap = snapshot_dir / db_file.name
        shutil.copy2(db_file, snap)
        wal = db_file.parent / f"{db_file.name}-wal"
        if wal.exists() and wal.stat().st_size > 0:
            shutil.copy2(wal, snapshot_dir / wal.name)

        with contextlib.closing(sqlite3.connect(snap)) as con:
            rows = con.execute("SELECT data FROM gen_metadata").fetchall()

        for (blob,) in rows:
            decoded = _decode_generation(blob)
            if decoded is None:
                continue
            usage, model = decoded
            gens += 1
            total.add(usage)
            if model not in model_usages:
                model_usages[model] = ModelUsage(
                    model_key=model,
                    provider="antigravity",
                    model=model,
                )
            model_usages[model].usage.add(usage)
            model_usages[model].turns += 1
    except Exception:
        pass

    return total, model_usages, gens


def _read_meta_cwd(meta_file: Path) -> str:
    """读取 <sid>.meta 中的 cwd。"""
    try:
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return str(data.get("cwd") or "")
    except Exception:
        pass
    return ""


def collect(days: int | None = None) -> list[SessionStats]:
    """采集并聚合全部 antigravity-acp 会话统计。"""
    conversations_dir = Path.home() / ".gemini" / "antigravity-acp" / "conversations"
    zed_threads = get_zed_threads(ZED_AGENT_ID)

    # 会话 ID = 会话库文件名（去 .db）∪ Zed 线程
    db_sids: set[str] = set()
    if conversations_dir.exists():
        db_sids = {p.stem for p in conversations_dir.glob("*.db")}
    all_sids = db_sids | set(zed_threads.keys())

    sessions: list[SessionStats] = []
    now = datetime.now(timezone.utc)

    temp_dir = tempfile.mkdtemp(prefix="ag_read_")
    try:
        snapshot_dir = Path(temp_dir)
        for sid in sorted(all_sids):
            thread_info = zed_threads.get(sid, {})
            if not isinstance(thread_info, dict):
                thread_info = {}

            db_file = conversations_dir / f"{sid}.db"
            if db_file.exists():
                total_usage, model_usages, gens = _parse_conversation_db(
                    db_file, snapshot_dir
                )
            else:
                total_usage, model_usages, gens = TokenUsage(), {}, 0

            cwd = _read_meta_cwd(conversations_dir / f"{sid}.meta")
            updated_at = thread_info.get("updated_at", "")
            created_at = thread_info.get("created_at", "")
            title = thread_info.get("title", "")
            folder_paths = thread_info.get("folder_paths", [])

            if days is not None and updated_at:
                try:
                    clean_time = updated_at.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(clean_time)
                    if (now - dt).total_seconds() > days * 86400:
                        continue
                except Exception:
                    pass

            sessions.append(SessionStats(
                agent=AGENT_NAME,
                session_id=sid,
                title=title,
                folder_paths=folder_paths,
                cwd=cwd,
                session_file=str(db_file) if db_file.exists() else "",
                created_at=created_at,
                updated_at=updated_at,
                turns=gens,
                user_messages=0,
                assistant_messages=gens,
                total_usage=total_usage,
                model_usages=model_usages,
            ))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    sessions.sort(key=lambda x: x.updated_at or "", reverse=True)
    return sessions
