"""检查状态（state.json）读写。

结构：
{
  "last_check": "2026-09-21T12:00:00",
  "seen": {"<sec_uid>": ["<aweme_id>", ...]}
}

seen 记录每个博主已见过的全部 aweme_id，作为新视频判定的差集基准；
原子写（临时文件 + os.replace）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class State:
    """state.json 的内存形态。"""

    last_check: str | None = None
    seen: dict[str, set[str]] = field(default_factory=dict)


def _atomic_write_json(path: Path, payload: object) -> None:
    """同目录临时文件写入后 os.replace，避免写到一半的 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def load_state(state_path: Path) -> State:
    """读取 state.json；不存在或损坏时返回空状态（视为首次检查）。"""
    if not state_path.exists():
        return State()
    try:
        # utf-8-sig 兼容手工编辑可能带出的 BOM 头
        raw = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return State()
    return State(
        last_check=raw.get("last_check"),
        seen={
            sec_uid: set(ids)
            for sec_uid, ids in raw.get("seen", {}).items()
        },
    )


def save_state(state_path: Path, state: State) -> None:
    """原子写 state.json（seen 集合转稳定排序的列表）。"""
    _atomic_write_json(
        state_path,
        {
            "last_check": state.last_check,
            "seen": {
                sec_uid: sorted(ids)
                for sec_uid, ids in state.seen.items()
            },
        },
    )


def mark_checked(state: State, now: datetime | None = None) -> None:
    """更新上次检查时间戳。"""
    state.last_check = (now or datetime.now()).isoformat(timespec="seconds")
