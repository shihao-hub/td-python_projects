"""公共契约模块：HTTP / MCP / RPC / WS 五面契约的单一事实源（设计 §7）。

纯协议定义、零业务依赖：不 import sqlite、core（Service 层）或任何入口
模块。daemon（http_api/ws）绑定契约到业务实现；MCP 桥、rpc 壳、schema
导出只读本模块——契约与实现同源，不得手抄副本（AC-10）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

API_PREFIX = "/api/v1"

# -- 连接常量（daemon 与壳共享；发现优先级见 lifecycle/client） -----------------

DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8766
DEFAULT_WS_PORT = 8765
# 地址覆盖环境变量（对标 OLLAMA_HOST / DOCKER_HOST）
ZEDHUB_HOST_ENV = "ZEDHUB_HOST"
# 自动拉起后等待地址文件 + 握手的超时（秒）
SPAWN_WAIT_TIMEOUT_S = 10.0

# -- HTTP 端点声明 ---------------------------------------------------------------
# http_api.py 按本清单注册路由并绑定 api.call；schema 导出按同一清单输出契约。


@dataclass(frozen=True)
class HttpEndpoint:
    method: str                       # "GET" | "POST"
    path: str                         # 含 API_PREFIX
    summary: str
    api_method: str                   # api.call 方法名（daemon 侧绑定）
    query_params: tuple[str, ...] = ()  # 查询参数名（校验在 api 层）
    path_params: tuple[str, ...] = ()
    body_model: type[BaseModel] | None = None
    sse: bool = False                 # 支持 Accept: text/event-stream


class LinkBody(BaseModel):
    """POST /api/v1/sessions/link 请求体（sessions link 命令面）。"""

    project: str                      # 目录路径或子串
    source: str = "opencode"          # opencode | claude-code | codex | antigravity
    all_dirs: bool = False            # 命中多目录时逐目录处理
    target: str | None = None         # 强制统一目标工作区
    include_subagents: bool = False
    exact: bool = False               # true = 归一化后与目录路径完全相等才命中
    apply: bool = False               # 默认 dry-run；true 才写库


class ArchiveExportBody(BaseModel):
    """POST /api/v1/archive/export 请求体。"""

    project: str
    output: str                       # 归档文件输出路径（用户显式指定）
    include_archived: bool = False
    exact: bool = False               # true = 归一化后与目录路径完全相等才命中
    source: str = "opencode"          # opencode(v1) | claude-code | codex | antigravity(v2)


class ArchiveInspectBody(BaseModel):
    """POST /api/v1/archive/inspect 请求体。"""

    file: str


class ArchiveImportBody(BaseModel):
    """POST /api/v1/archive/import 请求体。"""

    file: str
    target: str                       # 目标目录（必须已存在）
    apply: bool = False


HEALTH = HttpEndpoint(
    method="GET",
    path=f"{API_PREFIX}/health",
    summary="存活探测与 buildID 握手（X-Zedhub-Build 头校验）。",
    api_method="",  # 基础设施端点，不经 api.call
)

HTTP_ENDPOINTS: tuple[HttpEndpoint, ...] = (
    HEALTH,
    HttpEndpoint(
        method="GET", path=f"{API_PREFIX}/threads", api_method="threads.list",
        summary="List agent threads, newest first.",
        query_params=("project", "agent", "archived", "since", "until", "search", "limit"),
    ),
    HttpEndpoint(
        method="GET", path=f"{API_PREFIX}/threads/{{thread_id}}", api_method="threads.show",
        summary="Show one thread by uuid.",
        path_params=("thread_id",),
    ),
    HttpEndpoint(
        method="GET", path=f"{API_PREFIX}/projects", api_method="projects",
        summary="Per-folder project statistics (multi-root threads count in every folder).",
    ),
    HttpEndpoint(
        method="GET", path=f"{API_PREFIX}/stats", api_method="stats",
        summary="Global overview: totals, agents, workspace combos, monthly activity.",
    ),
    HttpEndpoint(
        method="GET", path=f"{API_PREFIX}/sources", api_method="sources",
        summary="Agent source registry status (supported / not_implemented / unavailable).",
    ),
    HttpEndpoint(
        method="GET", path=f"{API_PREFIX}/sessions", api_method="sessions.list",
        summary="List agent sessions (default source: opencode), newest first.",
        query_params=("source", "project", "agent", "archived", "limit"),
    ),
    HttpEndpoint(
        method="GET", path=f"{API_PREFIX}/sessions/{{session_id}}", api_method="sessions.show",
        summary="Show one session by external id.",
        path_params=("session_id",),
    ),
    HttpEndpoint(
        method="GET", path=f"{API_PREFIX}/sessions/{{session_id}}/content", api_method="sessions.content",
        summary="Full session content: session meta + messages + parts.",
        path_params=("session_id",),
    ),
    HttpEndpoint(
        method="GET", path=f"{API_PREFIX}/search", api_method="search.sessions",
        summary="Search session metadata (title/agent/id/project) across the Zed index"
                " and OpenCode sessions; opencode content full-text search is not implemented yet.",
        query_params=("q", "agent", "project", "archived", "since", "until", "limit",
                      "include_unlinked"),
    ),
    HttpEndpoint(
        method="GET", path=f"{API_PREFIX}/stats/effort", api_method="stats.effort",
        summary="Startup model × effort report (degraded flags in payload, not errors).",
    ),
    HttpEndpoint(
        method="POST", path=f"{API_PREFIX}/sessions/link", api_method="sessions.link",
        summary="Backfill sessions into Zed index (source: opencode | claude-code | codex |"
                " antigravity; dry-run by default; apply=true writes). Non-opencode sources"
                " allow cross-directory mounting (same session_id, new thread per target dir).",
        body_model=LinkBody, sse=True,
    ),
    HttpEndpoint(
        method="POST", path=f"{API_PREFIX}/archive/export", api_method="archive.export",
        summary="Export project sessions to a portable SQLite archive"
                " (source: opencode=v1 | claude-code/codex/antigravity=v2).",
        body_model=ArchiveExportBody, sse=True,
    ),
    HttpEndpoint(
        method="POST", path=f"{API_PREFIX}/archive/inspect", api_method="archive.inspect",
        summary="Inspect an archive file: version, source_agent, table counts.",
        body_model=ArchiveInspectBody,
    ),
    HttpEndpoint(
        method="POST", path=f"{API_PREFIX}/archive/import", api_method="archive.import",
        summary="Import archive into local Zed/OpenCode (dry-run by default; apply=true writes).",
        body_model=ArchiveImportBody, sse=True,
    ),
)


def find_endpoint(method: str, path: str) -> HttpEndpoint | None:
    """精确或路径参数匹配端点声明。"""
    for ep in HTTP_ENDPOINTS:
        if ep.method != method:
            continue
        ep_parts = ep.path.strip("/").split("/")
        req_parts = path.strip("/").split("/")
        if len(ep_parts) != len(req_parts):
            continue
        if all(e.startswith("{") or e == r for e, r in zip(ep_parts, req_parts)):
            return ep
    return None


# GET query 参数的类型标注（query string 天然是 str；HTTP 层按此 coerce，
# 让 GET 与 POST body / RPC params 的类型校验口径一致）。参数名全局唯一。
QUERY_PARAM_TYPES: dict[str, str] = {
    "limit": "integer",
    "include_unlinked": "boolean",
}


# -- MCP 工具元数据（桥注册与 schema 导出同源） -----------------------------------


@dataclass(frozen=True)
class ParamDoc:
    name: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class ToolMeta:
    name: str                          # MCP 工具名（存量平名或新三段式）
    summary: str
    params: tuple[ParamDoc, ...] = ()
    api_method: str = ""               # 桥转发的 HTTP 端点对应业务方法
    legacy: bool = False               # 存量兼容名（FR-9，不迁移）
    result_type: str = "object"        # "array" | "object"（签名 return 标注用）


MCP_TOOLS: tuple[ToolMeta, ...] = (
    ToolMeta(
        name="threads_list", legacy=True, api_method="threads.list", result_type="array",
        summary="List agent threads, newest first.",
        params=(
            ParamDoc("project", "Substring match on project path."),
            ParamDoc("agent", "Exact agent id, e.g. 'opencode'."),
            ParamDoc("archived", "'no' = active only (default), 'only' = archived, 'all'."),
            ParamDoc("since", "YYYY-MM-DD or ISO datetime (local time)."),
            ParamDoc("until", "YYYY-MM-DD or ISO datetime (local time)."),
            ParamDoc("search", "Case-insensitive substring in title/agent/id."),
            ParamDoc("limit", "Cap results; omit for no cap."),
        ),
    ),
    ToolMeta(
        name="threads_show", legacy=True, api_method="threads.show",
        summary="Show one thread by uuid.",
        params=(ParamDoc("thread_id", "Thread uuid (see threads_list).", required=True),),
    ),
    ToolMeta(
        name="projects", legacy=True, api_method="projects", result_type="array",
        summary="Per-folder project statistics (multi-root threads count in every folder).",
    ),
    ToolMeta(
        name="stats", legacy=True, api_method="stats",
        summary="Global overview: totals, agents, workspace combos, monthly activity.",
    ),
    ToolMeta(
        name="zedhub.sessions.list", api_method="sessions.list", result_type="array",
        summary="List agent sessions (default source: opencode), newest first.",
        params=(
            ParamDoc("source", "Source id; only 'opencode' is implemented."),
            ParamDoc("project", "Substring match on session directory."),
            ParamDoc("agent", "Exact agent filter."),
            ParamDoc("archived", "'no' | 'only' | 'all' (default 'no')."),
            ParamDoc("limit", "Cap results; omit for no cap."),
        ),
    ),
    ToolMeta(
        name="zedhub.sessions.show", api_method="sessions.show",
        summary="Show one session by external id.",
        params=(ParamDoc("session_id", "Session id (see sessions.list).", required=True),),
    ),
    ToolMeta(
        name="zedhub.sessions.content", api_method="sessions.content",
        summary="Full session content: session meta + messages + parts.",
        params=(ParamDoc("session_id", "Session id (see sessions.list).", required=True),),
    ),
    ToolMeta(
        name="zedhub.search.sessions", api_method="search.sessions", result_type="object",
        summary="Search session metadata (title/agent/id/project) across the Zed index and"
                " OpenCode sessions (content full-text search not implemented yet).",
        params=(
            ParamDoc("q", "Keywords, whitespace-separated; every keyword must match."),
            ParamDoc("agent", "Exact agent id, e.g. 'opencode', 'claude-acp'."),
            ParamDoc("project", "Substring match on project path (case-insensitive)."),
            ParamDoc("archived", "'no' = active only (default), 'only' = archived, 'all'."),
            ParamDoc("since", "YYYY-MM-DD or ISO datetime (local time)."),
            ParamDoc("until", "YYYY-MM-DD or ISO datetime (local time)."),
            ParamDoc("limit", "Cap results; '0' means no cap (default 50)."),
            ParamDoc("include_unlinked",
                     "'true' also lists OpenCode sessions missing from the Zed index."),
        ),
    ),
    ToolMeta(
        name="zedhub.stats.effort", api_method="stats.effort",
        summary="Startup model × effort report (degraded flags in payload, not errors).",
    ),
)


def tool_description(meta: ToolMeta) -> str:
    """从元数据拼接工具描述（桥注册与文档同一来源）。"""
    lines = [meta.summary]
    if meta.params:
        lines.append("")
        lines.append("Params:")
        for p in meta.params:
            lines.append(f"  {p.name}: {p.description}")
    return "\n".join(lines)


# -- 兼容 JSON-RPC 方法表（冻结，不新增） -----------------------------------------

ARCHIVED_VALUES = ("no", "only", "all")

RPC_METHODS: dict[str, tuple[str, str]] = {
    # json-rpc method -> (http method, http path)
    "threads.list": ("GET", f"{API_PREFIX}/threads"),
    "threads.show": ("GET", f"{API_PREFIX}/threads/{{thread_id}}"),
    "projects": ("GET", f"{API_PREFIX}/projects"),
    "stats": ("GET", f"{API_PREFIX}/stats"),
}

# rpc.discover 输出用：参数 JSON Schema 片段（type/enum/default 等）
RPC_PARAM_SPECS: dict[str, dict] = {
    "project": {"type": "string"},
    "agent": {"type": "string"},
    "archived": {"type": "string", "enum": list(ARCHIVED_VALUES), "default": "no"},
    "since": {"type": "string"},
    "until": {"type": "string"},
    "search": {"type": "string"},
    "limit": {"type": "integer", "minimum": 0},
    "thread_id": {"type": "string"},
}

RPC_PARAM_DOCS: dict[str, str] = {
    "project": "Substring match on project path.",
    "agent": "Exact agent id, e.g. 'opencode'.",
    "archived": "'no' = active only (default), 'only' = archived, 'all'.",
    "since": "YYYY-MM-DD or ISO datetime (local time).",
    "until": "YYYY-MM-DD or ISO datetime (local time).",
    "search": "Case-insensitive substring in title/agent/id.",
    "limit": "Cap results; omit for no cap.",
    "thread_id": "Thread uuid (see threads.list).",
}

RPC_METHOD_SPECS: dict[str, dict] = {
    "threads.list": {
        "summary": "List agent threads, newest first.",
        "params": ["project", "agent", "archived", "since", "until", "search", "limit"],
        "required": [],
        "result_type": "array",
    },
    "threads.show": {
        "summary": "Show one thread by uuid.",
        "params": ["thread_id"],
        "required": ["thread_id"],
        "result_type": "object",
    },
    "projects": {
        "summary": "Per-folder project statistics (multi-root threads count in every folder).",
        "params": [],
        "required": [],
        "result_type": "array",
    },
    "stats": {
        "summary": "Global overview: totals, agents, workspace combos, monthly activity.",
        "params": [],
        "required": [],
        "result_type": "object",
    },
}

# -- WebSocket 白名单与帧信封（学习性冻结通道） -----------------------------------

WS_METHODS: tuple[str, ...] = ("threads.list", "stats")

WS_FREEZE_NOTE = (
    "WebSocket 通道是一次性学习实现：本轮实现后冻结，不再新增方法或能力；"
    "生产与自动化用途一律使用 HTTP API。"
)

# 帧协议文档摘要（schema 导出与 README 引用）
WS_PROTOCOL: dict = {
    "transport": "JSON text frames over WebSocket (binary frame -> close 1003)",
    "max_frame_bytes": 1024 * 1024,
    "max_connections": 8,
    "overload_close_code": 1013,
    "request": {"id": "number|string", "method": "string", "params": "object"},
    "response_ok": {"id": "number|string|null", "ok": True, "data": "...", "elapsed_ms": "number"},
    "response_error": {"id": "number|string|null", "ok": False, "error": {"code": "string", "message": "string"}},
    "methods": list(WS_METHODS),
    "origin_policy": "Origin/Host must be loopback (127.0.0.1|localhost|::1) or absent",
    "frozen": WS_FREEZE_NOTE,
}
