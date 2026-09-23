"""Data collector for Zed pi-acp sessions."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zed_pi_stats.models import ModelUsage, SessionStats, TokenUsage


def get_zed_threads() -> dict[str, dict[str, Any]]:
    """Safely read Zed's SQLite database sidebar_threads for pi-acp sessions."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return {}

    db_dir = Path(local_app_data) / "Zed" / "db" / "0-stable"
    db_file = db_dir / "db.sqlite"
    if not db_file.exists():
        return {}

    temp_dir = tempfile.mkdtemp(prefix="zed_db_read_")
    try:
        # Safe read: copy SQLite 3-files (main, wal, shm) to avoid lock conflicts
        for suffix in ["", "-wal", "-shm"]:
            src = db_dir / f"db.sqlite{suffix}"
            if src.exists():
                shutil.copy2(src, Path(temp_dir) / f"db.sqlite{suffix}")

        temp_db = Path(temp_dir) / "db.sqlite"
        with contextlib.closing(sqlite3.connect(temp_db)) as connection:
            rows = connection.execute(
                """
                SELECT session_id, title, folder_paths, created_at, updated_at
                FROM sidebar_threads
                WHERE agent_id = 'pi-acp'
                ORDER BY updated_at DESC
                """
            ).fetchall()
        results = {}
        for row in rows:
            sid, title, folder_paths, created_at, updated_at = row
            paths = [p for p in (folder_paths or "").split("\n") if p.strip()]
            results[str(sid)] = {
                "title": title or "",
                "folder_paths": paths,
                "created_at": created_at or "",
                "updated_at": updated_at or "",
            }
        return results
    except Exception:
        return {}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def get_pi_acp_session_map() -> dict[str, dict[str, Any]]:
    """Load ~/.pi/pi-acp/session-map.json."""
    map_path = Path.home() / ".pi" / "pi-acp" / "session-map.json"
    if not map_path.exists():
        return {}

    try:
        with open(map_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
                return data["sessions"]
    except Exception:
        pass
    return {}


def parse_session_jsonl(session_file: str | Path) -> tuple[TokenUsage, dict[str, ModelUsage], int, int, int]:
    """Parse a single pi session .jsonl file.

    Returns:
        (total_usage, model_usages, turns, user_messages, assistant_messages)
    """
    total_usage = TokenUsage()
    model_usages: dict[str, ModelUsage] = {}
    turns = 0
    user_messages = 0
    assistant_messages = 0
    current_model_key = "unknown"
    current_provider = ""
    current_model_name = ""

    path = Path(session_file)
    if not path.exists():
        return total_usage, model_usages, turns, user_messages, assistant_messages

    def _extract_usage(usage_dict: dict[str, Any]) -> TokenUsage:
        if not isinstance(usage_dict, dict):
            return TokenUsage()

        def _as_int(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        def _as_float(value: Any) -> float:
            try:
                return float(value or 0.0)
            except (TypeError, ValueError):
                return 0.0

        inp = _as_int(usage_dict.get("input"))
        out = _as_int(usage_dict.get("output"))
        cr = _as_int(usage_dict.get("cacheRead"))
        cw = _as_int(usage_dict.get("cacheWrite"))
        reas = _as_int(usage_dict.get("reasoning"))
        tot = _as_int(usage_dict.get("total")) or (inp + out + cr + cw)
        cost_raw = usage_dict.get("cost")
        cost_val = 0.0
        if isinstance(cost_raw, dict):
            cost_val = _as_float(cost_raw.get("total"))
        elif isinstance(cost_raw, (int, float, str)):
            cost_val = _as_float(cost_raw)
        return TokenUsage(
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cr,
            cache_write_tokens=cw,
            reasoning_tokens=reas,
            total_tokens=tot,
            cost=cost_val,
        )

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue

                entry_type = data.get("type")

                if entry_type == "model_change":
                    prov = data.get("provider", "") or ""
                    mod = data.get("model", "") or ""
                    if mod:
                        current_provider = prov
                        current_model_name = mod
                        current_model_key = f"{prov}/{mod}" if prov else mod

                elif entry_type == "message":
                    msg = data.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    role = msg.get("role")

                    if role == "user":
                        user_messages += 1
                    elif role == "assistant":
                        assistant_messages += 1
                        turns += 1

                        prov = msg.get("provider") or current_provider
                        mod = msg.get("responseModel") or msg.get("model") or current_model_name
                        if not mod:
                            mod = "unknown"
                        model_key = f"{prov}/{mod}" if prov else mod

                        raw_usage = msg.get("usage")
                        if raw_usage:
                            u = _extract_usage(raw_usage)
                            total_usage.add(u)
                            if model_key not in model_usages:
                                model_usages[model_key] = ModelUsage(
                                    model_key=model_key,
                                    provider=prov or "",
                                    model=mod or "",
                                )
                            model_usages[model_key].usage.add(u)
                            model_usages[model_key].turns += 1

                    elif role == "toolResult":
                        raw_usage = msg.get("usage")
                        if raw_usage:
                            u = _extract_usage(raw_usage)
                            total_usage.add(u)
                            tool_key = "tools/summaries"
                            if tool_key not in model_usages:
                                model_usages[tool_key] = ModelUsage(
                                    model_key=tool_key,
                                    provider="tools",
                                    model="summaries",
                                )
                            model_usages[tool_key].usage.add(u)

                elif entry_type in ("usage", "compaction", "branch_summary"):
                    raw_usage = data.get("usage")
                    if raw_usage:
                        u = _extract_usage(raw_usage)
                        total_usage.add(u)
                        prov = data.get("provider", "") or "system"
                        mod = data.get("model", "") or entry_type
                        k = f"{prov}/{mod}" if prov else mod
                        if k not in model_usages:
                            model_usages[k] = ModelUsage(
                                model_key=k,
                                provider=prov,
                                model=mod,
                            )
                        model_usages[k].usage.add(u)

    except Exception:
        pass

    return total_usage, model_usages, turns, user_messages, assistant_messages


def collect_all_sessions(limit: int | None = None, days: int | None = None) -> list[SessionStats]:
    """Collect and aggregate stats for all pi-acp sessions."""
    zed_threads = get_zed_threads()
    session_map = get_pi_acp_session_map()

    # Collect all known session IDs
    all_sids = set(session_map.keys()) | set(zed_threads.keys())
    sessions: list[SessionStats] = []

    now = datetime.now(timezone.utc)

    for sid in all_sids:
        map_info = session_map.get(sid, {})
        if not isinstance(map_info, dict):
            map_info = {}
        thread_info = zed_threads.get(sid, {})
        if not isinstance(thread_info, dict):
            thread_info = {}

        session_file = map_info.get("sessionFile", "")
        cwd = map_info.get("cwd", "")
        updated_at = thread_info.get("updated_at") or map_info.get("updatedAt", "")
        created_at = thread_info.get("created_at", "")
        title = thread_info.get("title", "")
        folder_paths = thread_info.get("folder_paths", [])

        # Filter by days if specified
        if days is not None and updated_at:
            try:
                # updated_at ISO format
                clean_time = updated_at.replace("Z", "+00:00")
                dt = datetime.fromisoformat(clean_time)
                if (now - dt).total_seconds() > days * 86400:
                    continue
            except Exception:
                pass

        if session_file and os.path.exists(session_file):
            total_usage, model_usages, turns, user_msgs, asst_msgs = parse_session_jsonl(session_file)
        else:
            total_usage = TokenUsage()
            model_usages = {}
            turns = user_msgs = asst_msgs = 0

        # Create session stats
        s = SessionStats(
            session_id=sid,
            title=title,
            folder_paths=folder_paths,
            cwd=cwd,
            session_file=session_file,
            created_at=created_at,
            updated_at=updated_at,
            turns=turns,
            user_messages=user_msgs,
            assistant_messages=asst_msgs,
            total_usage=total_usage,
            model_usages=model_usages,
        )
        sessions.append(s)

    # Sort descending by updated_at
    sessions.sort(key=lambda x: x.updated_at or "", reverse=True)

    if limit is not None and limit > 0:
        sessions = sessions[:limit]

    return sessions
