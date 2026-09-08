"""Method registry — the single source of truth for programmatic access.

Both ``zedhub rpc`` (JSON-RPC 2.0) and ``zedhub mcp`` (MCP tools) resolve
method names and validate params through this module, so the two protocol
fronts can never drift apart. Payload field names match the CLI JSON
envelope (``data`` shapes are identical); datetimes are local-time ISO
strings, same as the CLI contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .core.model import Overview, ProjectStat, Thread
from .core.service import Service

# JSON-RPC 2.0 reserved codes live in rpc.py; here we only raise ApiError
# subclasses and let each protocol front map them to its own wire format.
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
NOT_FOUND = -32001
SERVER_ERROR = -32000

ARCHIVED_VALUES = ("no", "only", "all")


class ApiError(Exception):
    """Application-level error that is reportable through the protocol."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# -- serialization helpers (shared with the CLI envelope) --------------------


def dump_threads(items: list[Thread]) -> list[dict]:
    # datetime 一律转本地时区再序列化(CLI 契约,勿回退)
    def loc(t: Thread) -> Thread:
        return t.model_copy(
            update={
                "created_at": t.created_at.astimezone() if t.created_at else None,
                "updated_at": t.updated_at.astimezone() if t.updated_at else None,
                "interacted_at": t.interacted_at.astimezone() if t.interacted_at else None,
            }
        )

    return [loc(t).model_dump(mode="json") for t in items]


def dump_projects(items: list[ProjectStat]) -> list[dict]:
    out = []
    for p in items:
        p = p.model_copy(update={"last_activity": p.last_activity.astimezone() if p.last_activity else None})
        out.append(p.model_dump(mode="json"))
    return out


def dump_stats(overview: Overview) -> dict:
    return overview.model_dump(mode="json")


# -- param coercion -----------------------------------------------------------


def _opt_str(params: dict, key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(INVALID_PARAMS, f"param '{key}' must be a string, got {type(value).__name__}")
    return value or None


def _opt_int(params: dict, key: str) -> int | None:
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(INVALID_PARAMS, f"param '{key}' must be an integer, got {type(value).__name__}")
    return value


def _opt_date(params: dict, key: str) -> datetime | None:
    raw = _opt_str(params, key)
    if raw is None:
        return None
    for candidate in (raw, raw + "T00:00:00"):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    raise ApiError(INVALID_PARAMS, f"param '{key}' is not a valid date/ISO datetime: {raw}")


def _archived(params: dict) -> str:
    value = params.get("archived", "no")
    if value not in ARCHIVED_VALUES:
        raise ApiError(INVALID_PARAMS, f"param 'archived' must be one of {ARCHIVED_VALUES}, got {value!r}")
    return value


# -- method implementations (params dict -> JSON-ready payload) ---------------


def _threads_list(svc: Service, params: dict) -> Any:
    items = svc.list_threads(
        project=_opt_str(params, "project"),
        agent=_opt_str(params, "agent"),
        archived=_archived(params),
        since=_opt_date(params, "since"),
        until=_opt_date(params, "until"),
        search=_opt_str(params, "search"),
        limit=_opt_int(params, "limit"),
    )
    return dump_threads(items)


def _threads_show(svc: Service, params: dict) -> Any:
    tid = _opt_str(params, "thread_id")
    if tid is None:
        raise ApiError(INVALID_PARAMS, "param 'thread_id' is required")
    try:
        thread = svc.get_thread(tid)
    except LookupError as exc:
        raise ApiError(NOT_FOUND, str(exc)) from exc
    return dump_threads([thread])[0]


def _projects(svc: Service, params: dict) -> Any:
    return dump_projects(svc.projects())


def _stats(svc: Service, params: dict) -> Any:
    return dump_stats(svc.stats())


METHODS: dict[str, Any] = {
    "threads.list": _threads_list,
    "threads.show": _threads_show,
    "projects": _projects,
    "stats": _stats,
}

# 参数说明,供 MCP tool 描述 / rpc.discover / 文档生成复用(单一事实源)
PARAM_DOCS: dict[str, str] = {
    "project": "Substring match on project path.",
    "agent": "Exact agent id, e.g. 'opencode'.",
    "archived": "'no' = active only (default), 'only' = archived, 'all'.",
    "since": "YYYY-MM-DD or ISO datetime (local time).",
    "until": "YYYY-MM-DD or ISO datetime (local time).",
    "search": "Case-insensitive substring in title/agent/id.",
    "limit": "Cap results; omit for no cap.",
    "thread_id": "Thread uuid (see threads.list).",
}

# 参数 JSON Schema 片段(type/enum/default 等),rpc.discover 输出用
PARAM_SPECS: dict[str, dict] = {
    "project": {"type": "string"},
    "agent": {"type": "string"},
    "archived": {"type": "string", "enum": list(ARCHIVED_VALUES), "default": "no"},
    "since": {"type": "string"},
    "until": {"type": "string"},
    "search": {"type": "string"},
    "limit": {"type": "integer", "minimum": 0},
    "thread_id": {"type": "string"},
}

# 方法元数据:summary + 参数列表 + 必填参数 + 返回类型;
# rpc.discover 的 descriptor 与 MCP tool 描述都从这里生成
METHOD_SPECS: dict[str, dict] = {
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


def call(method: str, params: dict, service: Service) -> Any:
    """Dispatch one method call; returns a JSON-ready payload.

    Raises ApiError for invalid params / not found / unknown method;
    anything else escapes to the caller (protocol fronts map unexpected
    exceptions to their internal-error code).
    """
    fn = METHODS.get(method)
    if fn is None:
        raise ApiError(
            METHOD_NOT_FOUND,
            f"method not found: {method} (available: {', '.join(sorted(METHODS))})",
        )
    return fn(service, params if isinstance(params, dict) else {})
