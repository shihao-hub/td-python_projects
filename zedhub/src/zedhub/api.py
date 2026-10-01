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

from .core.writes import noop_progress

from pydantic import BaseModel

from .core.errors import (
    InvalidParamsError,
    MethodNotSupportedError,
    SchemaError,
    SnapshotError,
    ZedhubError,
)
from .core.model import Overview, ProjectStat, Session, SessionContent, Thread
from .core.model import SearchRequest, SearchResult
from .core.opencode_repo import OpencodeDb
from .core.repo import ZedDb
from .core.service import ThreadService
from .core.snapshot import open_opencode_ro, open_snapshot
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


def _opt_bool(params: dict, key: str, default: bool = False) -> bool:
    """布尔参数：body 直传 bool；query string 传 "true"/"1"/"yes" 等。"""
    value = params.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
    raise InvalidParamsError(f"param '{key}' must be a boolean, got {value!r}")


def _archived(params: dict) -> str:
    value = params.get("archived", "no")
    if value not in ("no", "only", "all"):
        raise InvalidParamsError(
            f"param 'archived' must be one of ('no', 'only', 'all'), got {value!r}"
        )
    return value


# -- Zed 方法（归档基线语义，AC-2） ----------------------------------------------


def _threads_list(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
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


def _threads_show(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    tid = _opt_str(params, "thread_id")
    if tid is None:
        raise InvalidParamsError("param 'thread_id' is required")
    with open_snapshot(ctx.zed_db) as snap:
        with ZedDb(snap) as db:
            return dump_threads([ThreadService(db).get_thread(tid)])[0]


def _projects(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    with open_snapshot(ctx.zed_db) as snap:
        with ZedDb(snap) as db:
            return dump_projects(ThreadService(db).projects())


def _stats(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    with open_snapshot(ctx.zed_db) as snap:
        with ZedDb(snap) as db:
            return dump_stats(ThreadService(db).stats())


# -- source 状态与 OpenCode 会话方法 --------------------------------------------


def _sources(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
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


def _sessions_list(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    src = get_source(_opt_str(params, "source"))
    sessions = src.list_sessions(
        SessionListRequest(
            project=_opt_str(params, "project"),
            agent=_opt_str(params, "agent"),
            archived=_archived(params),
            limit=_opt_int(params, "limit"),
        ),
        db=ctx.opencode_db,
    )
    return _attach_zed_link(sessions, ctx)


def _sessions_show(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    sid = _opt_str(params, "session_id")
    if sid is None:
        raise InvalidParamsError("param 'session_id' is required")
    src = get_source(params.get("source"))
    return _attach_zed_link([src.get_session(sid, db=ctx.opencode_db)], ctx)[0]


def _sessions_content(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    sid = _opt_str(params, "session_id")
    if sid is None:
        raise InvalidParamsError("param 'session_id' is required")
    src = get_source(params.get("source"))
    content = src.get_content(sid, db=ctx.opencode_db)
    linked = _zed_session_map(ctx)
    content.session.zed_thread_id = linked.get(sid) if linked else None
    return dump_content(content)


def _stats_effort(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    from .core.analytics import resolve_effort_report

    report = resolve_effort_report(ctx.opencode_db)
    out = _local(report, ("generated_at", "db_mtime"))
    return out


# -- 会话元数据检索 ---------------------------------------------------------------

# API 默认返回条数（limit=0 表示不限制，由调用方显式指定）
SEARCH_DEFAULT_LIMIT = 50


def dump_search(result: SearchResult) -> dict:
    out = result.model_dump(mode="json")
    out["hits"] = [
        _local(h, ("created_at", "updated_at", "interacted_at")) for h in result.hits
    ]
    return out


def _search(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    """元数据检索：Zed 索引为主表；OpenCode 源不可用时降级而非失败。"""
    from .core.search import SearchService

    limit = _opt_int(params, "limit")
    request = SearchRequest(
        q=_opt_str(params, "q"),
        agent=_opt_str(params, "agent"),
        project=_opt_str(params, "project"),
        archived=_archived(params),
        since=_opt_date(params, "since"),
        until=_opt_date(params, "until"),
        limit=SEARCH_DEFAULT_LIMIT if limit is None else limit,
        include_unlinked=_opt_bool(params, "include_unlinked"),
    )

    with open_snapshot(ctx.zed_db) as snap:
        with ZedDb(snap) as db:
            threads = db.load_threads()

    sessions: list[Session] | None = None
    degraded: list[str] = []
    try:
        with open_opencode_ro(ctx.opencode_db) as opened:
            oc = OpencodeDb(
                opened.con, db_path=opened.db_path, using_snapshot=opened.using_snapshot
            )
            # 归档过滤在检索层统一裁决，这里取全量会话（session 表为百行量级）
            sessions = oc.list_sessions(archived="all")
    except ZedhubError as exc:
        degraded.append(f"OpenCode 数据源不可用，结果仅含 Zed 索引：{exc}")

    result = SearchService(threads=threads, sessions=sessions).search(request)
    result.degraded = degraded
    return dump_search(result)


def _sessions_link(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    from .core.linking import run_link

    project = _opt_str(params, "project")
    if project is None:
        raise InvalidParamsError("param 'project' is required")
    target = params.get("target")
    if target is not None and not isinstance(target, str):
        raise InvalidParamsError("param 'target' must be a string")
    return run_link(
        project=project,
        source=_opt_str(params, "source") or "opencode",
        all_dirs=bool(params.get("all_dirs")),
        target=target,
        include_subagents=bool(params.get("include_subagents")),
        apply=bool(params.get("apply")),
        zed_db=ctx.zed_db,
        opencode_db=ctx.opencode_db,
        progress=progress,
    )


def _archive_export(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    from .core.archive import export_archive

    project = _opt_str(params, "project")
    output = _opt_str(params, "output")
    if project is None or output is None:
        raise InvalidParamsError("params 'project' and 'output' are required")
    return export_archive(
        project=project, output=output,
        include_archived=bool(params.get("include_archived")),
        exact=bool(params.get("exact")),
        source=_opt_str(params, "source") or "opencode",
        zed_db=ctx.zed_db, opencode_db=ctx.opencode_db,
        progress=progress,
    )


def _archive_inspect(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    from .core.archive import inspect_archive

    file = _opt_str(params, "file")
    if file is None:
        raise InvalidParamsError("param 'file' is required")
    return inspect_archive(file)


def _archive_import(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    from .core.migration import import_archive

    file = _opt_str(params, "file")
    target = _opt_str(params, "target")
    if file is None or target is None:
        raise InvalidParamsError("params 'file' and 'target' are required")
    return import_archive(
        file=file, target=target, apply=bool(params.get("apply")),
        zed_db=ctx.zed_db, opencode_db=ctx.opencode_db,
        progress=progress,
    )


# -- 注册表 -----------------------------------------------------------------------

METHODS: dict[str, Callable[..., Any]] = {
    "threads.list": _threads_list,
    "threads.show": _threads_show,
    "projects": _projects,
    "stats": _stats,
    "sources": _sources,
    "sessions.list": _sessions_list,
    "sessions.show": _sessions_show,
    "sessions.content": _sessions_content,
    "search.sessions": _search,
    "stats.effort": _stats_effort,
    "sessions.link": _sessions_link,
    "archive.export": _archive_export,
    "archive.inspect": _archive_inspect,
    "archive.import": _archive_import,
}


def call(
    method: str,
    params: dict,
    ctx: CallContext,
    progress=None,
) -> Any:
    """分发一次方法调用，返回 JSON-ready payload。

    业务方法抛 ZedhubError 子类（入口层映射到各自呈现）；未注册方法抛
    MethodNotSupportedError；其他异常逃逸给调用方按 internal_error 处理。
    """
    fn = METHODS.get(method)
    if fn is None:
        raise MethodNotSupportedError(
            f"method not supported: {method} (available: {', '.join(sorted(METHODS))})"
        )
    return fn(params if isinstance(params, dict) else {}, ctx, progress or noop_progress)
