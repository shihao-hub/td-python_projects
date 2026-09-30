"""OpenCode 会话源：把 OpencodeDb 包装成 AgentSource（薄适配器）。

每次调用独立打开只读连接（ro 直连优先、快照兜底），请求级拥有并关闭；
不跨请求复用 SQLite 句柄（NFR-1/设计 §3）。
"""

from __future__ import annotations

from ..model import Session, SessionContent, SessionListRequest
from ..opencode_repo import OpencodeDb
from ..snapshot import default_opencode_db_path, open_opencode_ro
from .base import AgentSource, Availability, Capability, SourceInfo, opencode_db_available

SOURCE_ID = "opencode"


class OpencodeSource(AgentSource):
    @property
    def info(self) -> SourceInfo:
        available = opencode_db_available()
        return SourceInfo(
            source_id=SOURCE_ID,
            display_name="OpenCode",
            availability=(
                Availability.SUPPORTED
                if available
                else Availability.UNAVAILABLE
            ),
            capabilities=[
                Capability.SESSIONS,
                Capability.CONTENT,
                Capability.EFFORT,
                Capability.EXPORT,
                Capability.LINK,
                Capability.IMPORT,
            ],
            note=None if available else f"数据库不存在: {default_opencode_db_path()}",
        )

    def list_sessions(self, request: SessionListRequest) -> list[Session]:
        with open_opencode_ro() as od:
            db = OpencodeDb(od.con, db_path=od.db_path, using_snapshot=od.using_snapshot)
            return db.list_sessions(
                project=request.project,
                agent=request.agent,
                archived=request.archived,
                limit=request.limit,
            )

    def get_session(self, external_id: str) -> Session:
        with open_opencode_ro() as od:
            db = OpencodeDb(od.con, db_path=od.db_path, using_snapshot=od.using_snapshot)
            return db.get_session(external_id)

    def get_content(self, external_id: str) -> SessionContent:
        with open_opencode_ro() as od:
            db = OpencodeDb(od.con, db_path=od.db_path, using_snapshot=od.using_snapshot)
            return db.get_session_content(external_id)
