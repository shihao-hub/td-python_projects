"""跨 agent 的源能力协议与注册表（FR-10 / NFR-6）。

- 结构化会话源当前仅 `opencode`；claude-code/codex/antigravity 已注册为
  **EXPORT-only 文件级源**（仅 `archive export`，schema v2，不支持会话
  查询——查询一律 `source_not_supported`，不得伪造为空结果，AC-13）；
- Pi 未实现，查询 `source_not_supported`；
- `source_id` 全局唯一；注册表只增不改已有条目语义。
- source 方法不向入口层暴露数据库连接、游标或 agent 专属表对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..errors import SourceNotSupportedError
from ..model import Session, SessionContent, SessionListRequest

# 规划中但未实现的 source（Availability.NOT_IMPLEMENTED 的固定清单）
PLANNED_SOURCES: tuple[str, ...] = ()

SOURCE_ALIASES: dict[str, str] = {
    "claude-acp": "claude-code",
    "codex-acp": "codex",
    "antigravity-acp": "antigravity",
    "pi-acp": "pi",
}


class Availability(str, Enum):
    SUPPORTED = "supported"              # 本版本实现了该源
    NOT_IMPLEMENTED = "not_implemented"  # 规划中但未实现
    UNAVAILABLE = "unavailable"          # 已实现但本机数据源缺失/不可读


class Capability(str, Enum):
    THREADS = "threads"
    SESSIONS = "sessions"
    CONTENT = "content"
    DISCOVERY = "discovery"
    EFFORT = "effort"
    EXPORT = "export"
    LINK = "link"
    IMPORT = "import"


@dataclass
class SourceInfo:
    source_id: str                      # "opencode" | ...
    display_name: str
    availability: Availability
    capabilities: list[Capability] = field(default_factory=list)
    note: str | None = None             # unavailable 原因等


class AgentSource:
    """agent 数据源协议：session/message/part 侧的读写能力边界。

    方法接受可选 ``db`` 路径（daemon 启动参数透传，覆盖默认探测）。
    Zed 索引不实现本协议（它是独立索引源，由 ZedDb + ThreadService 提供
    thread 查询与 session_id 关联）；只有 agent 会话源注册为 AgentSource。
    """

    info: SourceInfo

    def list_sessions(self, request: SessionListRequest, *, db: Path | None = None) -> list[Session]:
        raise NotImplementedError

    def get_session(self, external_id: str, *, db: Path | None = None) -> Session:
        raise NotImplementedError

    def get_content(self, external_id: str, *, db: Path | None = None) -> SessionContent:
        raise NotImplementedError


# 本版本注册表（sources/__init__.py 填充）
SOURCES: dict[str, AgentSource] = {}


def get_source(source_id: str | None) -> AgentSource:
    """按 id 取已实现 source；未注册 id 一律 source_not_supported。"""
    sid = (source_id or "opencode").strip()
    sid = SOURCE_ALIASES.get(sid, sid)
    src = SOURCES.get(sid)
    if src is None:
        planned = "（规划中，尚未实现）" if sid in PLANNED_SOURCES else ""
        raise SourceNotSupportedError(
            f"数据源未支持: {sid}{planned}；已支持: {', '.join(sorted(SOURCES)) or '（无）'}"
        )
    return src


def list_source_infos() -> list[SourceInfo]:
    """注册表状态 + 未实现源的可视化清单（sources 查询用）。"""
    infos = [src.info for src in SOURCES.values()]
    infos.extend(
        SourceInfo(
            source_id=sid,
            display_name=sid,
            availability=Availability.NOT_IMPLEMENTED,
            capabilities=[],
            note="规划中，本版本未实现",
        )
        for sid in PLANNED_SOURCES
        if sid not in SOURCES
    )
    return infos


def opencode_db_available() -> bool:
    """OpenCode 数据库文件是否可探测（info.availability 依据，轻量存在性检查）。"""
    from ..snapshot import default_opencode_db_path

    return Path(default_opencode_db_path()).exists()
