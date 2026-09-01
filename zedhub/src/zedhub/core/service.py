"""Filtering and aggregation over Zed threads — the heart of zedhub."""

from __future__ import annotations

from datetime import datetime

from .model import Overview, ProjectStat, Thread, WorkspaceCombo
from .repo import ZedDb

ArchivedFilter = str  # "no" | "only" | "all"


class NotFoundError(LookupError):
    pass


class Service:
    def __init__(self, db: ZedDb) -> None:
        self.db = db

    # -- threads -----------------------------------------------------------

    def list_threads(
        self,
        *,
        project: str | None = None,
        agent: str | None = None,
        archived: ArchivedFilter = "no",
        since: datetime | None = None,
        until: datetime | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[Thread]:
        """Filter threads. ``archived`` mirrors Zed's own default of hiding
        archived threads; pass ``all``/``only`` to see them."""
        q = (search or "").strip().casefold()

        def utc(dt: datetime) -> datetime:
            # naive input (e.g. from "2026-08-01") is interpreted as local time
            return dt.astimezone() if dt.tzinfo is None else dt

        since, until = utc(since) if since else None, utc(until) if until else None
        out: list[Thread] = []
        for t in self.db.load_threads():
            if project and not any(project.casefold() in p.casefold() for p in t.projects):
                continue
            if agent and t.agent_id != agent:
                continue
            if archived == "no" and t.archived:
                continue
            if archived == "only" and not t.archived:
                continue
            stamp = t.updated_at or t.created_at
            if since and (stamp is None or stamp < since):
                continue
            if until and (stamp is None or stamp > until):
                continue
            if q and q not in t.title.casefold() and q not in t.id.casefold() and q not in t.agent_id.casefold():
                continue
            out.append(t)
        out.sort(key=lambda t: t.updated_at or t.created_at or datetime.min, reverse=True)
        if limit is not None and limit >= 0:
            out = out[:limit]
        return out

    def get_thread(self, thread_id: str) -> Thread:
        tid = thread_id.strip().lower()
        for t in self.db.load_threads():
            if t.id == tid:
                return t
        raise NotFoundError(f"thread not found: {thread_id}")

    # -- aggregates --------------------------------------------------------

    def projects(self) -> list[ProjectStat]:
        """A project is a single folder path; a multi-root workspace thread
        contributes to every folder it was opened with."""
        stats: dict[str, ProjectStat] = {}
        for t in self.db.load_threads():
            for p in t.projects:
                s = stats.get(p)
                if s is None:
                    s = ProjectStat(path=p, total=0, active=0, archived=0, agents={})
                    stats[p] = s
                s.total += 1
                s.archived += 1 if t.archived else 0
                s.active += 0 if t.archived else 1
                s.agents[t.agent_id] = s.agents.get(t.agent_id, 0) + 1
                stamp = t.updated_at or t.created_at
                if stamp and (s.last_activity is None or stamp > s.last_activity):
                    s.last_activity = stamp
        return sorted(stats.values(), key=lambda s: s.last_activity or datetime.min, reverse=True)

    def stats(self) -> Overview:
        threads = self.db.load_threads()
        agents: dict[str, int] = {}
        combos: dict[tuple[str, ...], int] = {}
        monthly: dict[str, int] = {}
        archived = 0
        for t in threads:
            agents[t.agent_id] = agents.get(t.agent_id, 0) + 1
            combos[tuple(t.projects)] = combos.get(tuple(t.projects), 0) + 1
            archived += 1 if t.archived else 0
            stamp = t.updated_at or t.created_at
            if stamp:
                key = f"{stamp.year:04d}-{stamp.month:02d}"
                monthly[key] = monthly.get(key, 0) + 1
        return Overview(
            total_threads=len(threads),
            active=len(threads) - archived,
            archived=archived,
            agents=agents,
            projects=len({p for t in threads for p in t.projects}),
            workspace_combos=[
                WorkspaceCombo(paths=list(k), threads=v)
                for k, v in sorted(combos.items(), key=lambda kv: -kv[1])
            ],
            monthly=dict(sorted(monthly.items())),
        )
