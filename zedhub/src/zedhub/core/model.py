"""公共领域模型（pydantic）：对外契约的唯一形状。

CLI `--json`、HTTP API、RPC、MCP 的载荷都是这些类的 `model_dump(mode="json")`
投影；通用字段不依赖任何 agent 专有表名或原始 JSON（NFR-6）。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel

# -- Zed 索引侧 ---------------------------------------------------------------


class Thread(BaseModel):
    id: str
    session_id: str | None = None
    agent_id: str
    title: str
    archived: bool
    projects: list[str]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    interacted_at: datetime | None = None


class ProjectStat(BaseModel):
    path: str
    total: int
    active: int
    archived: int
    agents: dict[str, int]
    last_activity: datetime | None = None


class WorkspaceCombo(BaseModel):
    paths: list[str]
    threads: int


class Overview(BaseModel):
    total_threads: int
    active: int
    archived: int
    agents: dict[str, int]
    projects: int
    workspace_combos: list[WorkspaceCombo]
    monthly: dict[str, int]


# -- 通用会话模型（跨 agent source，FR-1/FR-10/NFR-6） ------------------------
# 通用字段不暴露任何 agent 专有表名、ID 格式或原始 JSON；
# agent 专属扩展须由各 source 提供带命名空间、字段白名单的独立 DTO。


class ModelRef(BaseModel):
    """通用模型引用：不透出 agent 原始 JSON。"""

    provider: str | None = None
    model_id: str | None = None
    variant: str | None = None  # 可选 effort/variant 通用投影


class Session(BaseModel):
    source_id: str                 # "opencode"；未来 "pi"/"claude-code"/...
    external_id: str
    title: str
    directory: str | None = None
    agent: str | None = None
    model: ModelRef | None = None
    created_at: datetime | None = None  # UTC aware；序列化时转本地（CLI 契约）
    updated_at: datetime | None = None
    archived: bool = False
    zed_thread_id: str | None = None    # Zed 索引关联；缺失 = None，不伪造


class MessagePart(BaseModel):
    id: str
    type: str
    text: str | None = None
    created_at: datetime | None = None


class Message(BaseModel):
    id: str
    role: str
    agent: str | None = None
    model_id: str | None = None
    created_at: datetime | None = None
    parts: list[MessagePart] = []


class SessionContent(BaseModel):
    session: Session
    messages: list[Message]


class SessionListRequest(BaseModel):
    project: str | None = None   # directory 子串匹配（不区分大小写）
    agent: str | None = None
    archived: str = "no"         # "no" | "only" | "all"
    limit: int | None = None


def ms_to_dt(ms: int | None) -> datetime | None:
    """OpenCode 毫秒时间戳 → UTC aware datetime。"""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# -- 解析口径（归档基线唯一实现，zoc 的重复实现废弃） --------------------------


def blob_to_thread_id(blob: bytes | None) -> str:
    """Zed 的 thread_id 是 16 字节 BLOB；对外暴露为 uuid 字符串。"""
    if isinstance(blob, (bytes, bytearray)) and len(blob) == 16:
        return str(uuid.UUID(bytes=bytes(blob)))
    return (blob or b"").hex() if isinstance(blob, (bytes, bytearray)) else ""


_FRACTION_RE = re.compile(r"\.(\d{7,})")


def parse_ts(raw: str | None) -> datetime | None:
    """解析 Zed 的 ISO 时间戳；归一化 >6 位小数（如 ...805964800+00:00）。"""
    if not raw:
        return None
    normalized = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6], raw)
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_paths(raw: str | None) -> list[str]:
    """folder_paths 换行分隔；保持顺序、丢弃空行。"""
    if not raw:
        return []
    return [p for p in raw.split("\n") if p.strip()]
