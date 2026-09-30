"""OpenCode 会话源只读层（显式列 SQL，禁 SELECT *，NFR-2）。

OpencodeDb 包装一条只读连接（来自 snapshot.open_opencode_ro），提供
session/message/part 查询与 schema 三级探测（ocstat 口径）：
- full：session + message + event 齐全，可还原启动模型；
- basic：仅 session 可用，只能展示当前模型（调用方降级标记）；
- broken：session 必需列缺失，抛 SchemaError（不可安全降级）。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .errors import NotFoundError, SchemaError
from .model import Message, MessagePart, ModelRef, Session, SessionContent, ms_to_dt

# detect_mode 三级（口径对齐归档 ocstat）
MODE_BROKEN = "broken"
MODE_BASIC = "basic"
MODE_FULL = "full"

_SESSION_COLS = ("id", "model", "time_created")


@dataclass
class OpencodeMode:
    mode: str
    note: str = ""


def _parse_model_json(raw: str | None) -> ModelRef | None:
    """容错解析 session.model 列：JSON 对象 / 纯字符串 / null 均兼容（ocstat 口径）。"""
    if not raw:
        return None
    s = raw.strip()
    if s in ("", "null"):
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return ModelRef(model_id=s)
    if not isinstance(obj, dict):
        return ModelRef(model_id=str(obj))
    ref = ModelRef(
        provider=obj.get("providerID") or obj.get("provider"),
        model_id=obj.get("id") or obj.get("modelID"),
        variant=obj.get("variant") or None,
    )
    if ref.model_id is None and ref.provider is None:
        return None
    return ref


def _row_to_session(r: sqlite3.Row, *, source_id: str = "opencode") -> Session:
    return Session(
        source_id=source_id,
        external_id=r["id"],
        title=(r["title"] or "").strip(),
        directory=r["directory"],
        agent=r["agent"] or None,
        model=_parse_model_json(r["model"]),
        created_at=ms_to_dt(r["time_created"]),
        updated_at=ms_to_dt(r["time_updated"]) if "time_updated" in r.keys() else None,
        archived=r["time_archived"] is not None,
    )


class OpencodeDb:
    def __init__(
        self,
        con: sqlite3.Connection,
        *,
        db_path: Path | None = None,
        using_snapshot: bool = False,
    ) -> None:
        self.con = con
        self.db_path = db_path
        self.using_snapshot = using_snapshot
        self.mode = self.detect_mode()

    # -- schema 探测 ---------------------------------------------------------

    def _tables(self) -> set[str]:
        rows = self.con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {r["name"] for r in rows}

    def _cols(self, table: str) -> set[str]:
        rows = self.con.execute(f"PRAGMA table_info({table})").fetchall()
        return {r["name"] for r in rows}

    def detect_mode(self) -> OpencodeMode:
        """三级探测：broken 抛 SchemaError；basic/full 返回模式说明。"""
        tables = self._tables()
        missing: list[str] = []

        def check(table: str, need: tuple[str, ...]) -> bool:
            if table not in tables:
                missing.append(f"缺少表 {table}")
                return False
            cols = self._cols(table)
            for c in need:
                if c not in cols:
                    missing.append(f"表 {table} 缺少列 {c}")
                    return False
            return True

        if not check("session", _SESSION_COLS):
            raise SchemaError(
                "opencode 库结构无法识别（" + "; ".join(missing) + "），"
                "可能是 opencode 更新改了表结构，请升级 zedhub。"
            )
        if check("message", ("session_id", "data", "time_created")) and check(
            "event", ("type", "data")
        ):
            return OpencodeMode(MODE_FULL)
        return OpencodeMode(
            MODE_BASIC,
            "事件/消息表不可用，仅展示会话当前模型（无法还原启动档位）: "
            + "; ".join(missing),
        )

    # -- session 查询 ---------------------------------------------------------

    def list_sessions(
        self,
        *,
        project: str | None = None,
        agent: str | None = None,
        archived: str = "no",
        limit: int | None = None,
    ) -> list[Session]:
        rows = self.con.execute(
            "SELECT id, project_id, directory, title, version, agent, model,"
            "       parent_id, time_created, time_updated, time_archived"
            "  FROM session"
        ).fetchall()
        out: list[Session] = []
        q = (project or "").casefold()
        for r in rows:
            s = _row_to_session(r)
            if q and (s.directory is None or q not in s.directory.casefold()):
                continue
            if agent and s.agent != agent:
                continue
            if archived == "no" and s.archived:
                continue
            if archived == "only" and not s.archived:
                continue
            out.append(s)
        out.sort(key=lambda s: s.created_at or datetime.min, reverse=True)
        if limit is not None and limit >= 0:
            out = out[:limit]
        return out

    def get_session(self, session_id: str) -> Session:
        r = self.con.execute(
            "SELECT id, project_id, directory, title, version, agent, model,"
            "       parent_id, time_created, time_updated, time_archived"
            "  FROM session WHERE id = ?",
            (session_id,),
        ).fetchone()
        if r is None:
            raise NotFoundError(f"session not found: {session_id}")
        return _row_to_session(r)

    def get_session_content(self, session_id: str) -> SessionContent:
        """session 元数据 + message + part 完整对话（显式列）。"""
        session = self.get_session(session_id)

        messages: dict[str, Message] = {}
        skipped_rows = 0
        for mr in self.con.execute(
            "SELECT id, session_id, time_created, data FROM message"
            " WHERE session_id = ? ORDER BY time_created, id",
            (session_id,),
        ):
            try:
                mdata = json.loads(mr["data"]) if mr["data"] else {}
            except json.JSONDecodeError:
                skipped_rows += 1
                continue
            if not isinstance(mdata, dict):
                skipped_rows += 1
                continue
            messages[mr["id"]] = Message(
                id=mr["id"],
                role=mdata.get("role", "unknown"),
                agent=mdata.get("agent") or None,
                model_id=mdata.get("modelID") or None,
                created_at=ms_to_dt(mr["time_created"]),
                parts=[],
            )

        for pr in self.con.execute(
            "SELECT id, message_id, session_id, time_created, data FROM part"
            " WHERE session_id = ? ORDER BY time_created, id",
            (session_id,),
        ):
            try:
                pdata = json.loads(pr["data"]) if pr["data"] else {}
            except json.JSONDecodeError:
                skipped_rows += 1
                continue
            if not isinstance(pdata, dict):
                skipped_rows += 1
                continue
            part = MessagePart(
                id=pr["id"],
                type=pdata.get("type", "unknown"),
                text=pdata.get("text"),
                created_at=ms_to_dt(pr["time_created"]),
            )
            msg = messages.get(pr["message_id"])
            if msg is not None:
                msg.parts.append(part)

        return SessionContent(
            session=session,
            messages=sorted(messages.values(), key=lambda m: (m.created_at or datetime.min, m.id)),
        )

    # -- analytics 供给（设计 §4；坏 JSON 逐行跳过由调用方计数） ----------------

    def iter_sessions_raw(self) -> Iterator[tuple[str, str | None, str | None, int]]:
        """(id, model_json, version, time_created_ms) 全量迭代。"""
        for r in self.con.execute(
            "SELECT id, model, version, time_created FROM session"
        ):
            yield r["id"], r["model"], r["version"], r["time_created"]

    def iter_first_assistant(self) -> Iterator[tuple[str, int, int, dict | None]]:
        """(session_id, time_created_ms, msg_count_hint, data) 逐行流；
        data 为解析后的 dict（坏行 None），首条判定留给 analytics。"""
        for r in self.con.execute(
            "SELECT session_id, data, time_created FROM message ORDER BY time_created"
        ):
            try:
                d = json.loads(r["data"]) if r["data"] else None
            except json.JSONDecodeError:
                d = None
            if d is not None and not isinstance(d, dict):
                d = None
            yield r["session_id"], r["time_created"], 0, d

    def iter_model_events(self) -> Iterator[tuple[str, int, dict | None]]:
        """(session_id, time_updated_ms, info_dict)，event 行按 rowid 顺序。"""
        for r in self.con.execute(
            "SELECT data FROM event"
            " WHERE type IN ('session.created.1', 'session.updated.1') ORDER BY rowid"
        ):
            try:
                e = json.loads(r["data"]) if r["data"] else None
            except json.JSONDecodeError:
                e = None
            if not isinstance(e, dict):
                continue
            info = e.get("info")
            if not isinstance(info, dict):
                continue
            sid = e.get("sessionID")
            tinfo = info.get("time")
            t = tinfo.get("updated") if isinstance(tinfo, dict) else None
            if not sid or not t:
                continue
            yield sid, t, info
