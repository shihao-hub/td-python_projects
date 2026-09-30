"""source 注册表组装：本版本仅注册 opencode。"""

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
)
from .opencode_source import OpencodeSource, SOURCE_ID

# 注册表只增不改：opencode 是当前唯一已实现 agent 数据源
SOURCES[SOURCE_ID] = OpencodeSource()

__all__ = [
    "PLANNED_SOURCES",
    "SOURCES",
    "AgentSource",
    "Availability",
    "Capability",
    "SourceInfo",
    "get_source",
    "list_source_infos",
    "OpencodeSource",
    "SOURCE_ID",
]
