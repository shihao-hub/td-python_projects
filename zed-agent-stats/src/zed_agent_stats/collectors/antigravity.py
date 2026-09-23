"""antigravity-acp 采集器。

数据源（规划期已探明）：
- ``~/.gemini/antigravity-acp/conversations/<sid>.db``：每会话一个 SQLite，
  ``gen_metadata`` 表每次 LLM 生成一行，``data`` 列为无 .proto 的 protobuf blob。
  用 blackboxprotobuf 解码后按字段号提取：
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

import blackboxprotobuf

from zed_agent_stats.collectors._zed import get_zed_threads
from zed_agent_stats.models import ModelUsage, SessionStats, TokenUsage

ZED_AGENT_ID = "antigravity-acp"
AGENT_NAME = "antigravity"


def _field(container: Any, num: int) -> Any:
    """取 protobuf 解码 dict 中指定字段号的值（兼容重复字段列表取首个）。"""
    if not isinstance(container, dict):
        return None
    v = container.get(str(num))
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _as_text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    return ""


def _decode_generation(blob: bytes) -> tuple[TokenUsage, str] | None:
    """解码单条 gen_metadata：返回 (usage, model)，无法解析返回 None。"""
    try:
        msg, _typedef = blackboxprotobuf.decode_message(bytes(blob))
    except Exception:
        return None

    gen = _field(msg, 1)
    if not isinstance(gen, dict):
        return None

    usage_raw = _field(gen, 4)
    model_raw = _field(gen, 19)
    if not isinstance(usage_raw, dict):
        # 无 usage 的生成（如失败请求）跳过
        return None
    model = _as_text(model_raw) or "unknown"

    inp = _as_int(_field(usage_raw, 2))
    out = _as_int(_field(usage_raw, 3))
    reasoning = _as_int(_field(usage_raw, 5))
    # f9/f10 语义未定，保守忽略不计入 total（README 注明待校准）
    usage = TokenUsage(
        input_tokens=inp,
        output_tokens=out,
        reasoning_tokens=reasoning,
        total_tokens=inp + out + reasoning,
    )
    return usage, model


def _parse_conversation_db(db_file: Path) -> tuple[TokenUsage, dict[str, ModelUsage], int]:
    """快照读取单个会话库并聚合 gen_metadata。

    Returns:
        (total_usage, model_usages, generations)
    """
    total = TokenUsage()
    model_usages: dict[str, ModelUsage] = {}
    gens = 0

    temp_dir = tempfile.mkdtemp(prefix="ag_conv_read_")
    try:
        snap = Path(temp_dir) / db_file.name
        for suffix in ("", "-wal", "-shm"):
            src = db_file.parent / f"{db_file.name}{suffix}"
            if src.exists():
                shutil.copy2(src, Path(temp_dir) / f"{db_file.name}{suffix}")

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
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

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

    for sid in all_sids:
        thread_info = zed_threads.get(sid, {})
        if not isinstance(thread_info, dict):
            thread_info = {}

        db_file = conversations_dir / f"{sid}.db"
        if db_file.exists():
            total_usage, model_usages, gens = _parse_conversation_db(db_file)
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

    sessions.sort(key=lambda x: x.updated_at or "", reverse=True)
    return sessions
