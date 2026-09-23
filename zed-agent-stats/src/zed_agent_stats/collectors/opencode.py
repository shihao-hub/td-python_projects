"""opencode 采集器。

数据源（规划期已探明）：
- ``~/.local/share/opencode/opencode.db``（约 1.9GB，**禁止快照复制**）：
  以 ``file:...?mode=ro`` 只读 URI 直查；WAL 活跃写入下偶发锁，短重试。
- ``session`` 表自带聚合列：cost / tokens_input / tokens_output / tokens_reasoning /
  tokens_cache_read / tokens_cache_write / model(JSON) / directory / 标题 / ms epoch 时间戳。
- ``message.data`` JSON（assistant 消息）含 modelID/providerID/tokens/cost，
  用于会话内按模型细分。

覆盖范围：opencode.db 中全部会话（Zed 内 + 独立终端运行的 opencode），
Zed sidebar_threads 命中的会话补充 folder_paths；标题优先取 opencode 自身。
cost 字段存在但订阅 plan 常回传 0（如 zhipu coding plan），展示层以 ``-`` 标示。
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zed_agent_stats.collectors._zed import get_zed_threads
from zed_agent_stats.models import ModelUsage, SessionStats, TokenUsage

ZED_AGENT_ID = "opencode"
AGENT_NAME = "opencode"
_RETRY_TIMES = 3
_RETRY_INTERVAL = 0.5


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    """只读 URI 连接大库，WAL 锁下短重试。"""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    last_error: Exception | None = None
    for _ in range(_RETRY_TIMES):
        try:
            con = sqlite3.connect(uri, uri=True)
            con.execute("SELECT 1").fetchone()
            return con
        except sqlite3.OperationalError as e:
            last_error = e
            time.sleep(_RETRY_INTERVAL)
    raise RuntimeError(f"opencode.db 只读连接失败(重试 {_RETRY_TIMES} 次): {last_error}")


def _ms_to_iso(ms: Any) -> str:
    """毫秒 epoch → ISO 8601 (UTC)。"""
    try:
        ms_int = int(ms)
        if ms_int <= 0:
            return ""
        return datetime.fromtimestamp(ms_int / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _parse_model_json(model_raw: Any) -> tuple[str, str, str]:
    """解析 session.model JSON 列 → (model_key, provider, model)。"""
    try:
        data = json.loads(model_raw) if isinstance(model_raw, str) else model_raw
        if isinstance(data, dict):
            provider = str(data.get("providerID") or "")
            model = str(data.get("id") or "")
            key = f"{provider}/{model}" if provider else model
            return key, provider, model
    except Exception:
        pass
    return "unknown", "", ""


def _extract_message_usage(tokens: Any) -> TokenUsage:
    """从 message.data 的 tokens 结构提取 TokenUsage。"""
    u = TokenUsage()
    if not isinstance(tokens, dict):
        return u
    try:
        u.input_tokens = int(tokens.get("input") or 0)
        u.output_tokens = int(tokens.get("output") or 0)
        u.reasoning_tokens = int(tokens.get("reasoning") or 0)
        cache = tokens.get("cache")
        if isinstance(cache, dict):
            u.cache_read_tokens = int(cache.get("read") or 0)
            u.cache_write_tokens = int(cache.get("write") or 0)
        u.total_tokens = u.input_tokens + u.output_tokens + u.reasoning_tokens
    except (TypeError, ValueError):
        pass
    return u


def _message_stats(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """单次遍历 message 表，聚合每个会话的按模型用量与轮次。"""
    per_session: dict[str, dict[str, Any]] = {}
    try:
        rows = con.execute("SELECT session_id, data FROM message").fetchall()
    except Exception:
        return per_session

    for session_id, data_raw in rows:
        try:
            data = json.loads(data_raw)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue

        role = data.get("role")
        entry = per_session.setdefault(str(session_id), {
            "model_usages": {},
            "turns": 0,
            "user_messages": 0,
            "assistant_messages": 0,
        })

        if role == "user":
            entry["user_messages"] += 1
            continue
        if role != "assistant":
            continue

        entry["assistant_messages"] += 1
        usage = _extract_message_usage(data.get("tokens"))
        if usage.total_tokens <= 0 and not usage.input_tokens:
            continue
        entry["turns"] += 1

        provider = str(data.get("providerID") or "")
        model = str(data.get("modelID") or "unknown")
        key = f"{provider}/{model}" if provider else model
        if key not in entry["model_usages"]:
            entry["model_usages"][key] = ModelUsage(
                model_key=key, provider=provider, model=model,
            )
        entry["model_usages"][key].usage.add(usage)
        entry["model_usages"][key].turns += 1
        cost = data.get("cost")
        if isinstance(cost, (int, float)) and cost:
            entry["model_usages"][key].usage.cost += float(cost)

    return per_session


def collect(days: int | None = None) -> list[SessionStats]:
    """采集并聚合 opencode 会话统计（opencode.db 全量会话）。"""
    db_path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    if not db_path.exists():
        return []

    sessions: list[SessionStats] = []
    now = datetime.now(timezone.utc)
    zed_threads = get_zed_threads(ZED_AGENT_ID)

    con = _connect_readonly(db_path)
    try:
        rows = con.execute(
            """
            SELECT id, title, directory, cost, tokens_input, tokens_output,
                   tokens_reasoning, tokens_cache_read, tokens_cache_write,
                   time_created, time_updated
            FROM session
            """
        ).fetchall()
        message_stats = _message_stats(con)
    finally:
        con.close()

    for row in rows:
        (sid, title, directory, cost, t_in, t_out, t_reason,
         t_cr, t_cw, time_created, time_updated) = row

        updated_at = _ms_to_iso(time_updated)
        created_at = _ms_to_iso(time_created)

        if days is not None and updated_at:
            try:
                dt = datetime.fromisoformat(updated_at)
                if (now - dt).total_seconds() > days * 86400:
                    continue
            except Exception:
                pass

        # 会话级聚合列 → total_usage
        try:
            inp, out = int(t_in or 0), int(t_out or 0)
            reason, cr, cw = int(t_reason or 0), int(t_cr or 0), int(t_cw or 0)
            cost_val = float(cost or 0.0)
        except (TypeError, ValueError):
            inp = out = reason = cr = cw = 0
            cost_val = 0.0
        total_usage = TokenUsage(
            input_tokens=inp,
            output_tokens=out,
            reasoning_tokens=reason,
            cache_read_tokens=cr,
            cache_write_tokens=cw,
            total_tokens=inp + out + reason,
            cost=cost_val,
        )

        msg_entry = message_stats.get(str(sid), {})
        model_usages = msg_entry.get("model_usages", {})
        turns = msg_entry.get("turns", 0)
        user_messages = msg_entry.get("user_messages", 0)
        assistant_messages = msg_entry.get("assistant_messages", 0)

        thread_info = zed_threads.get(str(sid), {})
        if not isinstance(thread_info, dict):
            thread_info = {}
        folder_paths = thread_info.get("folder_paths", [])
        if not title:
            title = thread_info.get("title", "")

        sessions.append(SessionStats(
            agent=AGENT_NAME,
            session_id=str(sid),
            title=title or "",
            folder_paths=folder_paths,
            cwd=directory or "",
            session_file=str(db_path),
            created_at=created_at,
            updated_at=updated_at,
            turns=turns,
            user_messages=user_messages,
            assistant_messages=assistant_messages,
            total_usage=total_usage,
            model_usages=model_usages,
        ))

    sessions.sort(key=lambda x: x.updated_at or "", reverse=True)
    return sessions
