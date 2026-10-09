"""公共领域模型（pydantic）：对外契约的唯一形状。

CLI `--json`、HTTP API、RPC、MCP 的载荷都是这些类的 `model_dump(mode="json")`
投影；通用字段不依赖任何 agent 专有表名或原始 JSON（NFR-6）。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import Enum

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


class SessionScope(str, Enum):
    ALL = "all"
    ZED = "zed"
    EXTERNAL = "external"


# -- 会话元数据检索（一期：只搜元数据，不做正文全文索引） ---------------------


class SearchRequest(BaseModel):
    """检索请求（服务端唯一定义语义；GUI/CLI 只做参数传递）。"""

    q: str | None = None          # 空白分隔的多关键词，全部命中才算命中（AND）
    agent: str | None = None      # agent id 精确匹配
    project: str | None = None    # 项目路径子串匹配（不区分大小写）
    archived: str = "no"          # "no" | "only" | "all"
    since: datetime | None = None
    until: datetime | None = None
    limit: int | None = None      # 0 = 不限制；None = 调用方默认
    scope: SessionScope = SessionScope.ALL  # all / zed / external
    include_unlinked: bool = False  # 兼容旧客户端：补上所有外部会话


class SearchHit(BaseModel):
    """统一检索结果条目：Zed 索引线程与外部会话同形投影。"""

    kind: str                     # "zed_thread" | "external_session"
    title: str
    agent_id: str
    source_id: str | None = None
    mode: str | None = None       # 内部角色模式（如 OpenCode 的 build/plan/explore/general）
    management: str = "zed"      # "zed" | "external"
    thread_id: str | None = None  # Zed 索引 thread uuid
    session_id: str | None = None  # agent 侧会话 id
    projects: list[str] = []
    model: ModelRef | None = None  # OpenCode 侧可关联到时的模型信息
    archived: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    interacted_at: datetime | None = None
    matched_fields: list[str] = []  # title/agent/id/project（GUI 高亮用）
    zed_linked: bool = False        # 是否存在于 Zed 索引


class SearchResult(BaseModel):
    """检索结果信封：命中总数 + 分页后的条目 + 降级说明。"""

    query: SearchRequest
    total: int                    # 过滤后命中总数（不受 limit 截断）
    count: int                    # 本次返回条数
    degraded: list[str] = []      # 数据源降级说明（空 = 全部可用）
    hits: list[SearchHit] = []


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
