"""方法注册表：HTTP / WS 共用的业务分发事实源（设计 §5/§6）。

每个方法：params dict → JSON-ready payload。入口层（http_api/ws）与
schema 导出全部经此分发；壳（CLI/MCP/rpc）不 import 本模块——它们经
HTTP 调用 daemon。

时间序列化契约：datetime 一律转本地时区再输出（CLI 契约，勿回退）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from .core.errors import (
    InvalidParamsError,
    MethodNotSupportedError,
    SchemaError,
    SnapshotError,
)
from .core.model import Overview, ProjectStat, Session, SessionContent, Thread
from .core.repo import ZedDb
from .core.service import ThreadService
from .core.snapshot import open_snapshot
from .core.sources import get_source, list_source_infos
from .core.model import SessionListRequest

# -- 调用上下文（daemon 启动参数确定的库路径） -----------------------------------


class CallContext:
    def __init__(self, *, zed_db: Path | None = None, opencode_db: Path | None = None) -> None:
        self.zed_db = zed_db
        self.opencode_db = opencode_db


# -- 序列化（本地时区唯一实现） --------------------------------------------------


def _local(model: BaseModel, fields: tuple[str, ...]) -> dict:
    updates = {}
    for f in fields:
        v = getattr(model, f, None)
        if isinstance(v, datetime):
            updates[f] = v.astimezone()
    return model.model_copy(update=updates).model_dump(mode="json")


def dump_threads(items: list[Thread]) -> list[dict]:
    return [_local(t, ("created_at", "updated_at", "interacted_at")) for t in items]


def dump_projects(items: list[ProjectStat]) -> list[dict]:
    return [_local(p, ("last_activity",)) for p in items]


def dump_stats(overview: Overview) -> dict:
    return overview.model_dump(mode="json")


def dump_sessions(items: list[Session]) -> list[dict]:
    return [_local(s, ("created_at", "updated_at")) for s in items]


def dump_content(content: SessionContent) -> dict:
    out = dump_sessions([content.session])[0]
    msgs = []
    for m in content.messages:
        d = _local(m, ("created_at",))
        d["parts"] = [_local(p, ("created_at",)) for p in m.parts]
        msgs.append(d)
    out["messages"] = msgs
    return out


# -- 参数解析（params dict，严格类型） -------------------------------------------


def _opt_str(params: dict, key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidParamsError(f"param '{key}' must be a string, got {type(value).__name__}")
    return value or None


def _opt_int(params: dict, key: str) -> int | None:
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParamsError(f"param '{key}' must be an integer, got {type(value).__name__}")
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
    raise InvalidParamsError(f"param '{key}' is not a valid date/ISO datetime: {raw}")


def _archived(params: dict) -> str:
    value = params.get("archived", "no")
    if value not in ("no", "only", "all"):
        raise InvalidParamsError(
            f"param 'archived' must be one of ('no', 'only', 'all'), got {value!r}"
        )
    return value


# -- Zed 方法（归档基线语义，AC-2） ----------------------------------------------


def _threads_list(params: dict, ctx: CallContext) -> Any:
    with open_snapshot(ctx.zed_db) as snap:
        with ZedDb(snap) as db:
            items = ThreadService(db).list_threads(
                project=_opt_str(params, "project"),
                agent=_opt_str(params, "agent"),
                archived=_archived(params),
                since=_opt_date(params, "since"),
                until=_opt_date(params, "until"),
                search=_opt_str(params, "search"),
                limit=_opt_int(params, "limit"),
            )
            return dump_threads(items)


def _threads_show(params: dict, ctx: CallContext) -> Any:
    tid = _opt_str(params, "thread_id")
    if tid is None:
        raise InvalidParamsError("param 'thread_id' is required")
    with open_snapshot(ctx.zed_db) as snap:
        with ZedDb(snap) as db:
            return dump_threads([ThreadService(db).get_thread(tid)])[0]


def _projects(params: dict, ctx: CallContext) -> Any:
    with open_snapshot(ctx.zed_db) as snap:
        with ZedDb(snap) as db:
            return dump_projects(ThreadService(db).projects())


def _stats(params: dict, ctx: CallContext) -> Any:
    with open_snapshot(ctx.zed_db) as snap:
        with ZedDb(snap) as db:
            return dump_stats(ThreadService(db).stats())


# -- source 状态与 OpenCode 会话方法 --------------------------------------------


def _sources(params: dict, ctx: CallContext) -> Any:
    return [
        {
            "source_id": i.source_id,
            "display_name": i.display_name,
            "availability": i.availability.value,
            "capabilities": [c.value for c in i.capabilities],
            "note": i.note,
        }
        for i in list_source_infos()
    ]


def _zed_session_map(ctx: CallContext) -> dict[str, str] | None:
    """Zed 索引 session_id → thread_id；Zed 缺失时返回 None（关联标记降级，
    不伪造，AC-3）；schema 异常照常传播（真实数据问题应暴露）。"""
    try:
        with open_snapshot(ctx.zed_db) as snap:
            with ZedDb(snap) as db:
                return {t.session_id: t.id for t in db.load_threads() if t.session_id}
    except SnapshotError:
        return None


def _attach_zed_link(sessions: list[Session], ctx: CallContext) -> list[dict]:
    linked = _zed_session_map(ctx)
    for s in sessions:
        s.zed_thread_id = linked.get(s.external_id) if linked else None
    return dump_sessions(sessions)


def _sessions_list(params: dict, ctx: CallContext) -> Any:
    src = get_source(_opt_str(params, "source"))
    sessions = src.list_sessions(
        SessionListRequest(
            project=_opt_str(params, "project"),
            agent=_opt_str(params, "agent"),
            archived=_archived(params),
            limit=_opt_int(params, "limit"),
        )
    )
    return _attach_zed_link(sessions, ctx)


def _sessions_show(params: dict, ctx: CallContext) -> Any:
    sid = _opt_str(params, "session_id")
    if sid is None:
        raise InvalidParamsError("param 'session_id' is required")
    src = get_source(params.get("source"))
    return _attach_zed_link([src.get_session(sid)], ctx)[0]


def _sessions_content(params: dict, ctx: CallContext) -> Any:
    sid = _opt_str(params, "session_id")
    if sid is None:
        raise InvalidParamsError("param 'session_id' is required")
    src = get_source(params.get("source"))
    content = src.get_content(sid)
    linked = _zed_session_map(ctx)
    content.session.zed_thread_id = linked.get(sid) if linked else None
    return dump_content(content)


def _stats_effort(params: dict, ctx: CallContext) -> Any:
    from .core.analytics import resolve_effort_report

    report = resolve_effort_report(ctx.opencode_db)
    out = _local(report, ("generated_at", "db_mtime"))
    return out


# -- 注册表 -----------------------------------------------------------------------

METHODS: dict[str, Callable[[dict, CallContext], Any]] = {
    "threads.list": _threads_list,
    "threads.show": _threads_show,
    "projects": _projects,
    "stats": _stats,
    "sources": _sources,
    "sessions.list": _sessions_list,
    "sessions.show": _sessions_show,
    "sessions.content": _sessions_content,
    "stats.effort": _stats_effort,
}


def call(method: str, params: dict, ctx: CallContext) -> Any:
    """分发一次方法调用，返回 JSON-ready payload。

    业务方法抛 ZedhubError 子类（入口层映射到各自呈现）；未注册方法抛
    MethodNotSupportedError；其他异常逃逸给调用方按 internal_error 处理。
    """
    fn = METHODS.get(method)
    if fn is None:
        raise MethodNotSupportedError(
            f"method not supported: {method} (available: {', '.join(sorted(METHODS))})"
        )
    return fn(params if isinstance(params, dict) else {}, ctx)
