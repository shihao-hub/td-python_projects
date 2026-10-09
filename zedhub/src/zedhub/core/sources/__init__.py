"""source 注册表组装：opencode（全能力）+ 三文件级源（EXPORT-only）。"""

from __future__ import annotations

from .base import (
    PLANNED_SOURCES,
    SOURCES,
    AgentSource,
    Availability,
    Capability,
    SourceInfo,
    get_source,
    list_source_infos,
    session_source_ids,
)
from .file_sources import FILE_SOURCE_DISPLAY_NAMES, FileSource
from .opencode_source import OpencodeSource, SOURCE_ID

# 注册表只增不改：opencode 是唯一结构化 agent 数据源；
# claude-code/codex/antigravity 仅支持 archive export（schema v2 文件级迁移）
SOURCES[SOURCE_ID] = OpencodeSource()
for _sid in FILE_SOURCE_DISPLAY_NAMES:
    SOURCES[_sid] = FileSource(_sid)

__all__ = [
    "PLANNED_SOURCES",
    "SOURCES",
    "AgentSource",
    "Availability",
    "Capability",
    "SourceInfo",
    "get_source",
    "list_source_infos",
    "session_source_ids",
    "FileSource",
    "OpencodeSource",
    "SOURCE_ID",
]
