"""方法注册表：HTTP / WS 共用的业务分发事实源（设计 §5/§6）。

每个方法：params dict → JSON-ready payload。入口层（http_api/ws）与
schema 导出全部经此分发；壳（CLI/MCP/rpc）不 import 本模块——它们经
HTTP 调用 daemon。

时间序列化契约：datetime 一律转本地时区再输出（CLI 契约，勿回退）。
"""

from __future__ import annotations

from datetime import datetime, timezone
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
from .core.metadata_cache import MetadataCacheService
from .core.model import Overview, ProjectStat, Session, SessionContent, Thread
from .core.model import SearchRequest, SearchResult, SessionScope
from .core.opencode_repo import OpencodeDb
from .core.repo import ZedDb
from .core.service import ThreadService
from .core.snapshot import open_opencode_ro, open_snapshot
from .core.sources import Capability, get_source, list_source_infos, session_source_ids
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


def _scope(params: dict) -> SessionScope:
    value = params.get("scope", SessionScope.ALL.value)
    try:
        return SessionScope(value)
    except ValueError as exc:
        raise InvalidParamsError(
            "param 'scope' must be one of ('all', 'zed', 'external')"
        ) from exc


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
    """Zed 索引 session_id → thread_id；优先走缓存，异常时降级快照。"""
    try:
        threads = MetadataCacheService().get_threads(ctx.zed_db)
        return {t.session_id: t.id for t in threads if t.session_id}
    except Exception:
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
    source_id = _opt_str(params, "source")
    request = SessionListRequest(
        project=_opt_str(params, "project"),
        agent=_opt_str(params, "agent"),
        archived=_archived(params),
        limit=_opt_int(params, "limit"),
    )
    if source_id == "all":
        sessions: list[Session] = []
        try:
            cache = MetadataCacheService()
            all_cached = cache.get_sessions(opencode_db=ctx.opencode_db)
            for s in all_cached:
                if request.project and request.project.casefold() not in (s.directory or "").casefold():
                    continue
                if request.agent and s.agent != request.agent:
                    continue
                if request.archived == "only" and not s.archived:
                    continue
                if request.archived == "no" and s.archived:
                    continue
                sessions.append(s)
            if request.limit is not None and request.limit > 0:
                sessions = sessions[:request.limit]
        except Exception:
            sessions = []
            for src in list_source_infos():
                if Capability.SESSIONS not in src.capabilities:
                    continue
                try:
                    source_request = request.model_copy(update={"limit": None})
                    sessions.extend(
                        get_source(src.source_id).list_sessions(
                            source_request, db=ctx.opencode_db
                        )
                    )
                except ZedhubError:
                    continue
            sessions.sort(
                key=lambda s: s.updated_at or s.created_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            if request.limit is not None and request.limit > 0:
                sessions = sessions[:request.limit]
    else:
        src = get_source(source_id)
        sessions = src.list_sessions(request, db=ctx.opencode_db)
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


def _trajectory(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    """统一轨迹：source + session_id → TimelineEvent 列表（只读，不解析写库）。"""
    from pathlib import Path as _P

    from .core.agent_paths import locate_session_files
    from .core.trajectory import load_trajectory, resolve_by_directory

    sid = _opt_str(params, "session_id")
    if sid is None:
        raise InvalidParamsError("param 'session_id' is required")
    source = _opt_str(params, "source") or "claude-code"
    limit = _opt_int(params, "limit") or 2000
    if source == "opencode":
        return load_trajectory(source="opencode", files=[sid], db=ctx.opencode_db,
                               limit=min(limit, 2000))
    if source == "antigravity-desktop":
        base = _P.home() / ".gemini" / "antigravity" / "conversations"
        f = base / f"{sid}.db"
        files = [f] if f.is_file() else []
    else:
        files = locate_session_files(source, [sid]).get(sid, [])
    if not files:
        # Zed 索引 id 与源文件 sid 两套 ID：用线程目录+时间回退映射
        thread_id = _opt_str(params, "thread_id")
        if thread_id and (source == "antigravity-desktop" or source in session_source_ids()):
            with open_snapshot(ctx.zed_db) as snap:
                with ZedDb(snap) as db:
                    t = ThreadService(db).get_thread(thread_id)
            projects = getattr(t, "projects", []) or []
            near = t.created_at.timestamp() if getattr(t, "created_at", None) else 0.0
            files, matched, candidates = resolve_by_directory(
                source=source, directory=projects[0] if projects else "", near_ts=near)
            if files:
                out = load_trajectory(source=source, files=files, limit=min(limit, 2000))
                out["mapped_from"] = sid
                out["mapped_to"] = matched
                return out
            raise InvalidParamsError(
                f"trajectory not found: {source}/{sid}（本地源文件无此 id"
                f"{f'，同目录候选 {candidates} 个但时间差距过大' if candidates else ''}"
                "；终端直跑未进索引的会话后续支持按目录浏览）")
        raise InvalidParamsError(f"trajectory not found: {source}/{sid}")
    return load_trajectory(source=source, files=files, limit=min(limit, 2000))


def _sessions_skills(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    """单个会话中识别到的 Skills 调用及调用证据明细。"""
    from .core.skill_extractor import extract_session_skills

    sid = _opt_str(params, "session_id")
    if sid is None:
        raise InvalidParamsError("param 'session_id' is required")
    source = _opt_str(params, "source") or ""
    skills, invocations = extract_session_skills(
        source, sid, opencode_db=ctx.opencode_db
    )
    return {
        "session_id": sid,
        "source": source,
        "skills": skills,
        "invocations": [inv.to_dict() for inv in invocations],
    }


def _skills_list(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    """全局识别到的 Skills 汇总排行榜。"""
    limit = _opt_int(params, "limit")
    cache = MetadataCacheService()
    try:
        cache.get_sessions(opencode_db=ctx.opencode_db)
    except Exception:
        pass
    items = cache.get_skills_summary()
    if limit is not None and limit > 0:
        items = items[:limit]
    return items


# -- 会话元数据检索 ---------------------------------------------------------------

# API 默认返回条数（limit=0 表示不限制，由调用方显式指定）
SEARCH_DEFAULT_LIMIT = 50


def dump_search(result: SearchResult) -> dict:
    out = result.model_dump(mode="json")
    hits = []
    for h in result.hits:
        hit_dict = _local(h, ("created_at", "updated_at", "interacted_at"))
        if h.snippets:
            hit_dict["snippets"] = [
                _local(s, ("created_at",)) for s in h.snippets
            ]
        hits.append(hit_dict)
    out["hits"] = hits
    return out


def _search(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    """会话检索：元数据（主表 Zed）+ 可选正文全文检索（FTS5）。优先命中增量元数据缓存。"""
    from .core.search import SearchService

    limit = _opt_int(params, "limit")
    request = SearchRequest(
        q=_opt_str(params, "q"),
        agent=_opt_str(params, "agent"),
        project=_opt_str(params, "project"),
        skill=_opt_str(params, "skill"),
        archived=_archived(params),
        since=_opt_date(params, "since"),
        until=_opt_date(params, "until"),
        limit=SEARCH_DEFAULT_LIMIT if limit is None else limit,
        scope=_scope(params),
        include_unlinked=_opt_bool(params, "include_unlinked"),
        content=_opt_bool(params, "content") or False,
    )

    cache = MetadataCacheService()
    try:
        threads = cache.get_threads(ctx.zed_db)
    except Exception:
        with open_snapshot(ctx.zed_db) as snap:
            with ZedDb(snap) as db:
                threads = db.load_threads()

    sessions: list[Session] = []
    degraded: list[str] = []
    try:
        sessions = cache.get_sessions(opencode_db=ctx.opencode_db)
    except Exception as exc:
        degraded.append(f"会话缓存同步异常，已降级实时扫描: {exc}")
        for source_id in session_source_ids():
            try:
                source = get_source(source_id)
                sessions.extend(
                    source.list_sessions(
                        SessionListRequest(archived="all"), db=ctx.opencode_db
                    )
                )
            except ZedhubError as e:
                degraded.append(f"{source_id} 数据源不可用：{e}")

    result = SearchService(threads=threads, sessions=sessions).search(request)
    result.degraded = degraded
    return dump_search(result)


def _index_status(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    from .core.index import ContentIndexService

    status = ContentIndexService().get_status()
    out = status.model_dump(mode="json")
    if out.get("last_built_at"):
        out["last_built_at"] = _local(status, ("last_built_at",))["last_built_at"]
    return out


def _index_build(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    from .core.index import ContentIndexService

    rebuild = _opt_bool(params, "rebuild") or False
    stats = ContentIndexService().build_index(
        rebuild=rebuild,
        opencode_db=ctx.opencode_db,
        progress=progress,
    )
    # 同步刷新元数据缓存
    try:
        MetadataCacheService().invalidate()
    except Exception:
        pass
    return stats.model_dump(mode="json")


def _sessions_link(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    from .core.linking import run_link

    source = _opt_str(params, "source") or "opencode"
    project = _opt_str(params, "project")
    if project is None:
        raise InvalidParamsError("param 'project' is required")
    exact = _opt_bool(params, "exact")
    target = _opt_str(params, "target")
    all_dirs = _opt_bool(params, "all_dirs")
    apply = _opt_bool(params, "apply")
    include_subagents = _opt_bool(params, "include_subagents")

    report = run_link(
        source=source,
        project=project,
        exact=exact,
        target=target,
        all_dirs=all_dirs,
        apply=apply,
        include_subagents=include_subagents,
        zed_db=ctx.zed_db,
        opencode_db=ctx.opencode_db,
        progress=progress,
    )
    # 补登成功写库后刷新元数据缓存
    if apply:
        try:
            MetadataCacheService().invalidate()
        except Exception:
            pass
    return report.model_dump(mode="json")


def _archive_export(params: dict, ctx: CallContext, progress=noop_progress) -> Any:
    from .core.archive import export_archive

    project = _opt_str(params, "project")
    if project is None:
        raise InvalidParamsError("param 'project' is required")
    output = _opt_str(params, "output")
    if output is None:
        raise InvalidParamsError("param 'output' is required")
    include_archived = _opt_bool(params, "include_archived")
    exact = _opt_bool(params, "exact")
    source = _opt_str(params, "source") or "opencode"

    return export_archive(
        project=project, output=output, include_archived=include_archived,
        exact=exact, source=source,
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
    from .core.archive import import_archive

    file = _opt_str(params, "file")
    if file is None:
        raise InvalidParamsError("param 'file' is required")
    target = _opt_str(params, "target")
    if target is None:
        raise InvalidParamsError("param 'target' is required")

    res = import_archive(
        file=file, target=target, apply=bool(params.get("apply")),
        zed_db=ctx.zed_db, opencode_db=ctx.opencode_db,
        progress=progress,
    )
    if bool(params.get("apply")):
        try:
            MetadataCacheService().invalidate()
        except Exception:
            pass
    return res


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
    "index.status": _index_status,
    "index.build": _index_build,
    "stats.effort": _stats_effort,
    "trajectory.show": _trajectory,
    "sessions.skills": _sessions_skills,
    "skills.list": _skills_list,
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
