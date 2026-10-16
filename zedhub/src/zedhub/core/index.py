"""多源 Agent 会话正文全文检索服务（SQLite FTS5 + 纯标准库 CJK/代码分词）。

- 存储位置：%APPDATA%\\language_projects\\zedhub\\index\\content_search.db；
- 索引语料：OpenCode / Claude Code / Codex / Antigravity / Pi 五大源的纯文本消息；
- 分词算法：纯标准库 CJK 1-gram + 2-gram 结合英文代码标识符切分，零外部第三方依赖；
- 增量机制：基于 session.updated_at 记录跳过未变动会话，支持手动增量与全量重建。
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import paths
from .model import ContentSnippet, IndexBuildStats, IndexStatus, SessionListRequest, parse_ts
from .sources import get_source, session_source_ids

__all__ = [
    "ContentIndexService",
    "tokenize_text",
    "tokenize_query",
    "extract_snippet",
]

# 英文与代码标识符正则：保留字母、数字、下划线、短横线与点号
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_\-\.]+|[\u4e00-\u9fff]")

# 每次构建单条消息正文索引截断上限，避免巨大单行日志撑爆 FTS
_MAX_TEXT_LEN = 100_000

# 正文上下文摘录前后窗口半径（字符数）
_SNIPPET_RADIUS = 35


def tokenize_text(text: str) -> str:
    """纯标准库分词器：CJK 单字 + 双字 + 英文/代码小写标识符，空格分隔。"""
    if not text:
        return ""
    tokens: list[str] = []
    chunks = _TOKEN_RE.findall(text)
    cjk_buffer: list[str] = []
    for chunk in chunks:
        if len(chunk) == 1 and "\u4e00" <= chunk <= "\u9fff":
            cjk_buffer.append(chunk)
            tokens.append(chunk)
            if len(cjk_buffer) >= 2:
                tokens.append(cjk_buffer[-2] + cjk_buffer[-1])
        else:
            cjk_buffer.clear()
            tokens.append(chunk.lower())
    return " ".join(tokens)


def tokenize_query(query: str) -> list[str]:
    """将搜索 query 拆解为用于 FTS MATCH 的分词列表。"""
    tok_str = tokenize_text(query)
    return [t for t in tok_str.split() if t]


def extract_snippet(orig_text: str, tokens: list[str], raw_tokens: list[str]) -> str:
    """从原始正文中提取包含关键词的上下文片段（前后约 _SNIPPET_RADIUS 字符）。"""
    if not orig_text:
        return ""
    lower_text = orig_text.lower()
    pos = -1
    best_len = 0
    # 优先在原始文本中寻找用户原始输入词
    for cand in raw_tokens + tokens:
        if not cand:
            continue
        p = lower_text.find(cand.lower())
        if p != -1:
            pos = p
            best_len = len(cand)
            break

    if pos == -1:
        # 兜底直接返回前段
        res = orig_text[: _SNIPPET_RADIUS * 2].strip()
        return res + ("…" if len(orig_text) > _SNIPPET_RADIUS * 2 else "")

    start = max(0, pos - _SNIPPET_RADIUS)
    end = min(len(orig_text), pos + best_len + _SNIPPET_RADIUS)
    segment = orig_text[start:end].strip().replace("\r", " ").replace("\n", " ")
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(orig_text) else ""
    return f"{prefix}{segment}{suffix}"


def _report(progress: Callable | None, detail: str, pct: int | None = None) -> None:
    if not progress:
        return
    try:
        progress("index", detail, pct)
    except TypeError:
        try:
            progress("index", detail)
        except TypeError:
            progress(detail)


class ContentIndexService:
    """会话正文 FTS5 索引管理器与查询器。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or paths.index_db_path()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        self._init_tables(con)
        return con

    @staticmethod
    def _init_tables(con: sqlite3.Connection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS index_meta (
                session_key TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                updated_at TEXT,
                indexed_at TEXT NOT NULL,
                message_count INTEGER NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
                session_key UNINDEXED,
                source_id UNINDEXED,
                session_id UNINDEXED,
                role UNINDEXED,
                created_at UNINDEXED,
                orig_text UNINDEXED,
                tokenized
            )
            """
        )
        con.commit()

    def get_status(self) -> IndexStatus:
        """获取当前索引库的元数据状态。"""
        if not self.db_path.is_file():
            return IndexStatus()
        with sqlite3.connect(self.db_path) as con:
            row = con.execute(
                """
                SELECT count(session_key), coalesce(sum(message_count), 0), max(indexed_at)
                FROM index_meta
                """
            ).fetchone()
            sessions_count = row[0] if row else 0
            messages_count = row[1] if row else 0
            last_built = parse_ts(row[2]) if row and row[2] else None
            size = self.db_path.stat().st_size
            return IndexStatus(
                indexed_sessions=sessions_count,
                indexed_messages=messages_count,
                db_size_bytes=size,
                last_built_at=last_built,
            )

    def build_index(
        self,
        *,
        rebuild: bool = False,
        sources: list[str] | None = None,
        opencode_db: Path | None = None,
        progress: Callable[..., None] | None = None,
    ) -> IndexBuildStats:
        """扫描各源会话并增量构建正文 FTS5 索引。"""
        start_time = time.perf_counter()
        con = self._get_connection()
        stats = IndexBuildStats()

        if rebuild:
            _report(progress, "正在重置现有索引库…")
            con.execute("DELETE FROM content_fts")
            con.execute("DELETE FROM index_meta")
            con.commit()

        # 读取已索引会话的更新时间
        existing_meta: dict[str, str | None] = {}
        if not rebuild:
            for s_key, up_at in con.execute(
                "SELECT session_key, updated_at FROM index_meta"
            ).fetchall():
                existing_meta[s_key] = up_at

        target_sources = sources or session_source_ids()
        now_iso = datetime.now(timezone.utc).isoformat()

        for source_id in target_sources:
            try:
                src = get_source(source_id)
                session_list = src.list_sessions(SessionListRequest(archived="all"), db=opencode_db)
            except Exception as exc:
                _report(progress, f"数据源 {source_id} 读取失败，已跳过：{exc}")
                continue

            for session in session_list:
                stats.scanned_sessions += 1
                sid = session.external_id
                s_key = f"{source_id}:{sid}"
                sess_updated_str = (
                    session.updated_at.isoformat() if session.updated_at else None
                )

                if not rebuild and s_key in existing_meta:
                    if existing_meta[s_key] == sess_updated_str:
                        stats.skipped_sessions += 1
                        continue

                # 提取会话消息
                try:
                    content = src.get_content(sid, db=opencode_db)
                except Exception:
                    stats.skipped_sessions += 1
                    continue

                messages = content.messages or []
                rows_to_insert = []
                for m in messages:
                    text_parts = [
                        p.text for p in m.parts if p.type == "text" and p.text
                    ]
                    if not text_parts:
                        continue
                    full_text = "\n".join(text_parts).strip()
                    if not full_text:
                        continue
                    if len(full_text) > _MAX_TEXT_LEN:
                        full_text = full_text[:_MAX_TEXT_LEN]

                    tok = tokenize_text(full_text)
                    if not tok:
                        continue

                    m_time_iso = m.created_at.isoformat() if m.created_at else None
                    rows_to_insert.append(
                        (s_key, source_id, sid, m.role or "user", m_time_iso, full_text, tok)
                    )

                # 覆盖写入
                con.execute("DELETE FROM content_fts WHERE session_key = ?", (s_key,))
                con.execute("DELETE FROM index_meta WHERE session_key = ?", (s_key,))

                if rows_to_insert:
                    con.executemany(
                        """
                        INSERT INTO content_fts(session_key, source_id, session_id, role, created_at, orig_text, tokenized)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows_to_insert,
                    )
                con.execute(
                    """
                    INSERT INTO index_meta(session_key, source_id, session_id, updated_at, indexed_at, message_count)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (s_key, source_id, sid, sess_updated_str, now_iso, len(rows_to_insert)),
                )
                stats.updated_sessions += 1
                stats.indexed_messages += len(rows_to_insert)

            con.commit()

        con.close()
        stats.duration_ms = int((time.perf_counter() - start_time) * 1000)
        return stats

    def search_content(
        self,
        raw_tokens: list[str],
        *,
        limit_per_session: int = 2,
    ) -> dict[str, list[ContentSnippet]]:
        """在 FTS5 索引中搜索正文，返回 {session_id: [ContentSnippet, ...]}。"""
        if not raw_tokens or not self.db_path.is_file():
            return {}

        query_terms = []
        for raw in raw_tokens:
            toks = tokenize_query(raw)
            if toks:
                # 每个原始 query 词拆出的 token 必须全部命中
                query_terms.extend(toks)

        if not query_terms:
            return {}

        # FTS5 查询表达式：所有 token 必须全部匹配（AND）
        fts_match_query = " ".join(f'"{t}"' for t in query_terms)

        results: dict[str, list[ContentSnippet]] = {}
        with sqlite3.connect(self.db_path) as con:
            # 限制单次检索返回的消息行数，避免大库无意义提取
            cursor = con.execute(
                """
                SELECT session_id, role, created_at, orig_text
                FROM content_fts
                WHERE tokenized MATCH ?
                LIMIT 500
                """,
                (fts_match_query,),
            )
            for sid, role, cr_at, orig_text in cursor.fetchall():
                snippets = results.setdefault(sid, [])
                if len(snippets) >= limit_per_session:
                    continue
                snip_text = extract_snippet(orig_text, query_terms, raw_tokens)
                snippets.append(
                    ContentSnippet(
                        role=role or "user",
                        text=snip_text,
                        created_at=parse_ts(cr_at) if cr_at else None,
                    )
                )

        return results
