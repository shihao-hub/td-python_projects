"""文件级 agent 源（claude-code/codex/antigravity）：archive EXPORT + sessions LINK。

- 三源不做结构化会话解析（codex/claude JSONL 不投影、antigravity
  step_payload 是无公开 schema 的 protobuf），会话查询方法一律
  ``source_not_supported``，不伪造为空结果（AC-13）；
- 归档迁移走 ``archive export/import``（schema v2 整文件字节搬运）；
  补登走 ``sessions link --source <id>``（浅层元数据扫描，见 agent_sessions）；
- availability 按各源用户级数据根（~/.claude | ~/.codex | ~/.gemini）存在性。
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

from ..agent_paths import agent_data_root
from ..errors import SourceNotSupportedError
from ..model import Session, SessionContent, SessionListRequest
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
            capabilities=[Capability.EXPORT, Capability.LINK],
            note=note,
        )

    def _reject(self) -> NoReturn:
        raise SourceNotSupportedError(
            f"数据源 {self.source_id!r} {_EXPORT_LINK_NOTE}"
        )

    def list_sessions(self, request: SessionListRequest, *, db: Path | None = None) -> list[Session]:
        self._reject()

    def get_session(self, external_id: str, *, db: Path | None = None) -> Session:
        self._reject()

    def get_content(self, external_id: str, *, db: Path | None = None) -> SessionContent:
        self._reject()
