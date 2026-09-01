"""Pydantic models describing Zed agent sessions.

These models are zedhub's public contract: the JSON emitted by the CLI is
``model_dump(mode="json")`` of these classes, so any consumer (frontend,
scripts, future ``serve`` shell) only ever needs to know these shapes.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel

ThreadID = str


class Thread(BaseModel):
    id: ThreadID
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


def blob_to_thread_id(blob: bytes | None) -> str:
    """Zed stores thread_id as a 16-byte BLOB; expose it as a uuid string."""
    if isinstance(blob, (bytes, bytearray)) and len(blob) == 16:
        return str(uuid.UUID(bytes=bytes(blob)))
    return (blob or b"").hex() if isinstance(blob, (bytes, bytearray)) else ""


_FRACTION_RE = re.compile(r"\.(\d{7,})")


def parse_ts(raw: str | None) -> datetime | None:
    """Parse Zed's ISO timestamps; normalize >6-digit fractions (e.g. ...805964800+00:00)."""
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
    """folder_paths is newline-separated; keep order, drop empties."""
    if not raw:
        return []
    return [p for p in raw.split("\n") if p.strip()]
