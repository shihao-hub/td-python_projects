"""文件级 agent 源（claude-code/codex/antigravity）：archive EXPORT + sessions LINK。

- 三源不做结构化会话解析（codex/claude JSONL 不投影、antigravity
  step_payload 是无公开 schema 的 protobuf），会话查询方法一律
  ``source_not_supported``，不伪造为空结果（AC-13）；
- 归档迁移走 ``archive export/import``（schema v2 整文件字节搬运）；
  补登走 ``sessions link --source <id>``（浅层元数据扫描，见 agent_sessions）；
- availability 按各源用户级数据根（~/.claude | ~/.codex | ~/.gemini）存在性。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

from ..agent_paths import agent_data_root
from ..errors import SourceNotSupportedError
from ..agent_sessions import scan_agent_sessions
from ..model import Session, SessionContent, SessionListRequest, parse_ts
from .base import AgentSource, Availability, Capability, SourceInfo

# source id → 展示名（注册表顺序即 sources 查询展示顺序）
FILE_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "claude-code": "Claude Code (Zed ACP)",
    "codex": "Codex (Zed ACP)",
    "antigravity": "Antigravity (Zed ACP)",
}

_EXPORT_LINK_NOTE = (
    "支持 archive export/import（schema v2 整文件迁移）与 sessions link 补登"
    "（含跨目录挂载）；不支持会话查询"
)


class FileSource(AgentSource):
    """EXPORT/LINK 适配器：list/get/content 一律拒绝，不伪造数据。"""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    def _sessions(self, request: SessionListRequest) -> list[Session]:
        records = scan_agent_sessions(self.source_id)
        sessions = [
            Session(
                source_id=self.source_id,
                external_id=record.session_id,
                title=record.title,
                directory=record.directory or None,
                agent=self.source_id,
                created_at=parse_ts(record.time_created),
                updated_at=parse_ts(record.time_updated),
            )
            for record in records
            if record.directory
        ]
        project = (request.project or "").casefold()
        if project:
            sessions = [
                s for s in sessions
                if project in (s.directory or "").casefold()
            ]
        if request.agent:
            sessions = [s for s in sessions if s.agent == request.agent]
        if request.archived == "no":
            sessions = [s for s in sessions if not s.archived]
        elif request.archived == "only":
            sessions = [s for s in sessions if s.archived]
        sessions.sort(key=lambda s: s.updated_at or s.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        if request.limit is not None and request.limit > 0:
            sessions = sessions[:request.limit]
        return sessions

    @property
    def info(self) -> SourceInfo:
        try:
            root = agent_data_root(self.source_id)
            available = root.is_dir()
            note = None if available else f"数据根目录不存在: {root}"
        except Exception:  # 未知 source 不会注册到本类，防御性兜底
            available, note = False, "数据根不可探测"
        return SourceInfo(
            source_id=self.source_id,
            display_name=FILE_SOURCE_DISPLAY_NAMES.get(self.source_id, self.source_id),
            availability=(
                Availability.SUPPORTED if available else Availability.UNAVAILABLE
            ),
            capabilities=[Capability.SESSIONS, Capability.DISCOVERY, Capability.EXPORT, Capability.LINK],
            note=note,
        )

    def _reject(self) -> NoReturn:
        raise SourceNotSupportedError(
            f"数据源 {self.source_id!r} {_EXPORT_LINK_NOTE}"
        )

    def list_sessions(self, request: SessionListRequest, *, db: Path | None = None) -> list[Session]:
        return self._sessions(request)

    def get_session(self, external_id: str, *, db: Path | None = None) -> Session:
        sessions = self._sessions(SessionListRequest(archived="all"))
        for session in sessions:
            if session.external_id == external_id:
                return session
        raise SourceNotSupportedError(
            f"数据源 {self.source_id!r} 中未找到会话: {external_id}"
        )

    def get_content(self, external_id: str, *, db: Path | None = None) -> SessionContent:
        self._reject()
