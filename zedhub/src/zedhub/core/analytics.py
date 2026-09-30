"""启动模型与 effort 统计（ocstat 口径移植，FR-4 / AC-4 / AC-5）。

算法照抄归档 ocstat 已验证口径（235/235 吻合）：
1. session 全量取 id/model/version/time_created；
2. full 模式扫 message（按 time_created）：每会话首条
   role=assistant 且 agent!=title 且 modelID 非空 的消息时间戳；
3. 扫 event（session.created.1 / session.updated.1，按 rowid）构建每会话
   模型时间线，取 ≤ 首条消息时刻的最后生效模型；无命中回退首事件模型；
4. basic 模式仅当前模型（degraded 标记）；broken 由 OpencodeDb 构造时抛
   SchemaError。

纯函数、无状态：watch 只是 CLI 层重复调用；坏 JSON 逐行跳过并计数。
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from .config import EffortConfig, load_effort_config, lookup_effort
from .model import ModelRef
from .opencode_repo import MODE_BASIC, MODE_FULL, OpencodeDb, _parse_model_json
from .snapshot import open_opencode_ro

# -- effort 展示与排序（ocstat 口径） ------------------------------------------

_EFFORT_ORDER = {"max": 0, "high": 1, "medium": 2, "low": 3, "default": 4}


def _effort_rank(v: str) -> int:
    if v in _EFFORT_ORDER:
        return _EFFORT_ORDER[v]
    if v == "":
        return 5
    return 6


def _effort_sort_key(v: str) -> str:
    """「default→max」这类合并展示取 → 后面的真实档位参与排序。"""
    i = v.find("→")
    return v[i + len("→") :] if i >= 0 else v


def effort_less(a: str, b: str) -> bool:
    ka, kb = _effort_sort_key(a), _effort_sort_key(b)
    ra, rb = _effort_rank(ka), _effort_rank(kb)
    if ra != rb:
        return ra < rb
    return a < b


def resolve_effort(cfg: EffortConfig, provider: str | None, model: str | None, variant: str | None) -> str:
    """合并配置档位：variant 为 default/缺失且配置写死 effort 时展示
    「default→max」；真实档位变体本身就是档位，原样返回。"""
    v = variant or ""
    if v and v != "default":
        return v
    eff = lookup_effort(cfg, provider, model)
    if eff:
        return "default→" + eff
    return v if v else "-"


# -- 报告模型 -------------------------------------------------------------------


class EffortGroup(BaseModel):
    provider: str
    model: str
    effort: str
    sessions: int
    percent: float


class EffortReport(BaseModel):
    mode: str                        # full | basic
    note: str
    db_path: str
    db_size: int
    db_mtime: datetime | None
    using_snapshot: bool
    versions: list[str]
    cfg_merged: bool
    generated_at: datetime
    groups: list[EffortGroup]
    total: int
    no_msg_count: int
    startup_count: int
    skipped_rows: int = 0            # 坏 JSON 行计数（不致命）


# -- 内部统计结构 -----------------------------------------------------------------


@dataclass
class _SessionStat:
    version: str = ""
    created: int = 0
    msg_count: int = 0
    startup: ModelRef | None = None
    current: ModelRef | None = None


@dataclass
class _FirstMsg:
    t: int


@dataclass
class _EventEntry:
    t: int
    m: ModelRef


@dataclass
class _Accum:
    stats: dict[str, _SessionStat] = field(default_factory=dict)
    first: dict[str, _FirstMsg] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    timeline: dict[str, list[_EventEntry]] = field(default_factory=dict)
    skipped_rows: int = 0
    versions: set[str] = field(default_factory=set)


def _enrich_full(db: OpencodeDb, acc: _Accum) -> None:
    """首条非 title assistant 消息 + 事件时间线 → 启动模型。"""
    for sid, t, _, data in db.iter_first_assistant():
        acc.counts[sid] = acc.counts.get(sid, 0) + 1
        if data is None:
            acc.skipped_rows += 1
            continue
        if (
            data.get("role") == "assistant"
            and data.get("agent") != "title"
            and data.get("modelID")
            and sid not in acc.first
        ):
            acc.first[sid] = _FirstMsg(t=t)

    for sid, t, info in db.iter_model_events():
        model = info.get("model")
        if not isinstance(model, dict):
            acc.skipped_rows += 1
            continue
        ref = ModelRef(
            provider=model.get("providerID") or model.get("provider"),
            model_id=model.get("id") or model.get("modelID"),
            variant=model.get("variant") or None,
        )
        if ref.model_id is None:
            acc.skipped_rows += 1
            continue
        acc.timeline.setdefault(sid, []).append(_EventEntry(t=t, m=ref))

    for sid, st in acc.stats.items():
        st.msg_count = acc.counts.get(sid, 0)
        tl = acc.timeline.get(sid, [])
        tl.sort(key=lambda e: e.t)
        f = acc.first.get(sid)
        if f is None:
            continue
        hit: ModelRef | None = None
        for e in tl:
            if e.t <= f.t:
                hit = e.m
            else:
                break
        if hit is None and tl:
            hit = tl[0].m  # 无命中回退首事件模型（ocstat 口径）
        st.startup = hit


def resolve_startup(db: OpencodeDb, cfg: EffortConfig | None = None) -> EffortReport:
    """从 OpencodeDb 计算启动模型/effort 报告（纯函数）。"""
    if cfg is None:
        cfg, _ = load_effort_config()
    cfg_merged = len(cfg) > 0

    acc = _Accum()
    for sid, model_json, version, created in db.iter_sessions_raw():
        st = _SessionStat(created=created, current=_parse_model_json(model_json))
        if version:
            st.version = version
            acc.versions.add(version)
        acc.stats[sid] = st

    if db.mode.mode == MODE_FULL:
        _enrich_full(db, acc)

    db_size = 0
    db_mtime: datetime | None = None
    if db.db_path is not None:
        try:
            stat = db.db_path.stat()
            db_size, db_mtime = stat.st_size, datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        except OSError:
            pass

    # 分组：full 用启动模型，basic 用当前模型（降级口径）
    use_startup = db.mode.mode == MODE_FULL
    type GK = tuple[str, str]
    groups: dict[GK, dict[str, int]] = {}
    totals: dict[GK, int] = {}
    no_msg_count = startup_count = total = 0
    for sid, st in acc.stats.items():
        if use_startup:
            if st.msg_count == 0:
                no_msg_count += 1
            if st.startup is not None:
                startup_count += 1
        m = st.startup if use_startup else st.current
        if m is None or m.model_id is None:
            continue
        k: GK = (m.provider or "", m.model_id)
        v = resolve_effort(cfg, m.provider, m.model_id, m.variant)
        groups.setdefault(k, {})
        groups[k][v] = groups[k].get(v, 0) + 1
        totals[k] = totals.get(k, 0) + 1
        total += 1

    rows: list[EffortGroup] = []
    ordered_keys = sorted(totals, key=lambda k: (-totals[k], k[0] + "/" + k[1]))
    for k in ordered_keys:
        efforts = sorted(
            groups[k].keys(),
            key=functools.cmp_to_key(
                lambda a, b: -1 if effort_less(a, b) else (1 if effort_less(b, a) else 0)
            ),
        )
        for eff in efforts:
            n = groups[k][eff]
            rows.append(
                EffortGroup(
                    provider=k[0],
                    model=k[1],
                    effort=eff,
                    sessions=n,
                    percent=round(n * 100 / total, 1) if total else 0.0,
                )
            )

    return EffortReport(
        mode=db.mode.mode,
        note=db.mode.note,
        db_path=str(db.db_path) if db.db_path else "",
        db_size=db_size,
        db_mtime=db_mtime,
        using_snapshot=db.using_snapshot,
        versions=sorted(acc.versions),
        cfg_merged=cfg_merged,
        generated_at=datetime.now(timezone.utc),
        groups=rows,
        total=total,
        no_msg_count=no_msg_count,
        startup_count=startup_count,
        skipped_rows=acc.skipped_rows,
    )


def resolve_effort_report(db: str | Path | None = None) -> EffortReport:
    """daemon 侧入口：打开只读连接（ro 直连优先、快照兜底）后全量计算。"""
    with open_opencode_ro(db) as od:
        odb = OpencodeDb(od.con, db_path=od.db_path, using_snapshot=od.using_snapshot)
        return resolve_startup(odb)
