"""会话元数据检索（一期：标题/agent/id/项目路径 + 过滤条件）。

- 语义在本模块唯一定义，入口层（HTTP/CLI/MCP/GUI）只做参数解析与呈现；
- **Zed 索引是主表**：它代表「Zed 里看得见的会话」（覆盖 opencode/claude-acp/
  codex-acp/antigravity-acp/pi-acp 等），OpenCode 会话通过 session_id 关联上来
  补充 directory/model，并可在标题为空时兜底标题；
- `include_unlinked=True` 时补上未进 Zed 索引的 OpenCode 会话（kind=
  ``opencode_session``），默认不显示以避免与 Zed 视图重复；
- OpenCode 源不可用时由调用方降级（degraded 说明），本模块只管过滤与合并，
  不感知数据源可用性，便于脱离 SQL 单测；
- 正文全文检索（messages/parts）不在本期范围，正文语料与索引方案见
  `docs/plans/29-zedhub-session-search.md`「二期」。
"""

from __future__ import annotations

from datetime import datetime, timezone

from .model import SearchHit, SearchRequest, SearchResult, Session, SessionScope, Thread

# 时间排序哨兵：全局 tz-aware，避免与 naive datetime 比较时抛 TypeError
_MIN_TS = datetime.min.replace(tzinfo=timezone.utc)

# matched_fields 取值（GUI 高亮与契约稳定值）
FIELD_TITLE = "title"
FIELD_AGENT = "agent"
FIELD_ID = "id"
FIELD_PROJECT = "project"


def _utc(dt: datetime | None) -> datetime | None:
    """naive 输入（如 "2026-08-01"）按本地时间解释，与 threads.list 口径一致。"""
    if dt is None:
        return None
    return dt.astimezone() if dt.tzinfo is None else dt


def _tokens(q: str | None) -> list[str]:
    """空白分隔的多关键词（AND）；casefold 后匹配。"""
    return [t for t in (q or "").casefold().split() if t]


def _norm_path(p: str | None) -> str:
    """路径规范化：正反斜杠统一、去除末尾斜杠、小写，用于不敏感匹配。"""
    if not p:
        return ""
    return p.replace("\\", "/").rstrip("/").casefold()


class SearchService:
    """把 Zed 线程与 OpenCode 会话投影成统一条目后过滤。"""

    def __init__(self, *, threads: list[Thread], sessions: list[Session] | None) -> None:
        self.threads = threads
        self.sessions = sessions  # None = OpenCode 源不可用（降级）

    # -- 条目构建 ---------------------------------------------------------------

    def _hits(self, include_unlinked: bool, scope: SessionScope) -> list[SearchHit]:
        include_external = include_unlinked or scope in (SessionScope.ALL, SessionScope.EXTERNAL)
        by_id: dict[tuple[str, str], Session] = {
            (s.source_id, s.external_id): s for s in self.sessions or []
        }
        hits: list[SearchHit] = []
        linked: set[str] = set()

        for t in self.threads:
            sess = next(
                (s for (source, external_id), s in by_id.items() if external_id == t.session_id),
                None,
            ) if t.session_id else None
            if sess is not None:
                linked.add(sess.external_id)
            projects = t.projects or ([sess.directory] if sess and sess.directory else [])
            hits.append(
                SearchHit(
                    kind="zed_thread",
                    title=t.title or (sess.title if sess else ""),
                    agent_id=t.agent_id,
                    source_id=sess.source_id if sess else None,
                    management="zed",
                    thread_id=t.id,
                    session_id=t.session_id,
                    projects=projects,
                    model=sess.model if sess else None,
                    archived=t.archived,
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                    interacted_at=t.interacted_at,
                    zed_linked=True,
                )
            )

        if include_external:
            for s in self.sessions or []:
                if s.external_id in linked:
                    continue
                hits.append(
                    SearchHit(
                        kind="external_session",
                        title=s.title,
                        agent_id=s.agent or s.source_id,
                        source_id=s.source_id,
                        management="external",
                        thread_id=None,
                        session_id=s.external_id,
                        projects=[s.directory] if s.directory else [],
                        model=s.model,
                        archived=s.archived,
                        created_at=s.created_at,
                        updated_at=s.updated_at,
                        interacted_at=None,
                        zed_linked=False,
                    )
                )
        return hits

    # -- 匹配与过滤 -------------------------------------------------------------

    @staticmethod
    def _match_fields(hit: SearchHit, tokens: list[str]) -> list[str] | None:
        """全部 token 必须命中；返回命中的字段集合（无 token 时为全部为空的浏览模式）。"""
        if not tokens:
            return []
        haystack = {
            FIELD_TITLE: hit.title.casefold(),
            FIELD_AGENT: hit.agent_id.casefold(),
            FIELD_ID: " ".join(x for x in (hit.thread_id, hit.session_id) if x).casefold(),
            FIELD_PROJECT: "\n".join(hit.projects).casefold(),
        }
        matched: set[str] = set()
        for token in tokens:
            where = [name for name, text in haystack.items() if token in text]
            if not where:
                return None
            matched.update(where)
        return sorted(matched)

    @staticmethod
    def _stamp(hit: SearchHit) -> datetime:
        return hit.updated_at or hit.created_at or _MIN_TS

    def search(self, request: SearchRequest) -> SearchResult:
        tokens = _tokens(request.q)
        since, until = _utc(request.since), _utc(request.until)
        project = (request.project or "").casefold()
        norm_proj = _norm_path(request.project)

        hits: list[SearchHit] = []
        for hit in self._hits(request.include_unlinked, request.scope):
            if request.scope == SessionScope.ZED and hit.management != "zed":
                continue
            if request.scope == SessionScope.EXTERNAL and hit.management != "external":
                continue
            if request.agent and hit.agent_id != request.agent:
                continue
            if norm_proj and not any(
                project in p.casefold() or norm_proj in _norm_path(p)
                for p in hit.projects
            ):
                continue
            if request.archived == "no" and hit.archived:
                continue
            if request.archived == "only" and not hit.archived:
                continue
            stamp = self._stamp(hit)
            if since and stamp < since:
                continue
            if until and stamp > until:
                continue
            matched = self._match_fields(hit, tokens)
            if matched is None:
                continue
            hit.matched_fields = matched
            hits.append(hit)

        hits.sort(key=lambda h: (self._stamp(h), h.title.casefold()), reverse=True)
        total = len(hits)
        limit = request.limit
        if limit is not None and limit > 0:
            hits = hits[:limit]
        return SearchResult(query=request, total=total, count=len(hits), hits=hits)
