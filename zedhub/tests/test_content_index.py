from __future__ import annotations

import tempfile
from pathlib import Path

from zedhub.core.index import ContentIndexService, extract_snippet, tokenize_query, tokenize_text
from zedhub.core.model import Message, MessagePart, Session, SessionContent


def test_tokenize_text_mixed() -> None:
    text = "有什么视频录制软件啊，比如 OBS 或 zedhub_search-v2.0"
    tokens = tokenize_text(text)
    # 中文包含 1-gram 和 2-gram
    assert "视" in tokens
    assert "频" in tokens
    assert "视频" in tokens
    assert "录制" in tokens
    assert "软件" in tokens
    # 英文标识符小写且保持完整
    assert "obs" in tokens
    assert "zedhub_search-v2.0" in tokens


def test_extract_snippet() -> None:
    text = "前缀" * 40 + "有什么视频录制软件啊很方便" + "后缀" * 40
    snip = extract_snippet(text, ["录制"], ["录制"])
    assert "录制" in snip
    assert "…" in snip


def test_content_index_service_basic(tmp_path: Path) -> None:
    db_file = tmp_path / "test_index.db"
    service = ContentIndexService(db_file)

    # 初始状态为空
    status = service.get_status()
    assert status.indexed_sessions == 0
    assert status.indexed_messages == 0

    # 手动插入测试数据到数据库
    con = service._get_connection()
    s_key = "opencode:ses-1"
    raw_text = "我们在讨论 ZedHub 的全文检索方案"
    tok = tokenize_text(raw_text)
    con.execute(
        """
        INSERT INTO content_fts(session_key, source_id, session_id, role, created_at, orig_text, tokenized)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (s_key, "opencode", "ses-1", "user", "2026-10-15T12:00:00Z", raw_text, tok),
    )
    con.execute(
        """
        INSERT INTO index_meta(session_key, source_id, session_id, updated_at, indexed_at, message_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (s_key, "opencode", "ses-1", "2026-10-15T12:00:00Z", "2026-10-15T12:00:00Z", 1),
    )
    con.commit()
    con.close()

    # 状态更新
    status = service.get_status()
    assert status.indexed_sessions == 1
    assert status.indexed_messages == 1
    assert status.db_size_bytes > 0

    # 检索中文词命中
    res = service.search_content(["检索"])
    assert "ses-1" in res
    assert len(res["ses-1"]) == 1
    assert "检索" in res["ses-1"][0].text
    assert res["ses-1"][0].role == "user"

    # 检索英文代码命中
    res_en = service.search_content(["ZedHub"])
    assert "ses-1" in res_en

    # 检索不存在词未命中
    res_none = service.search_content(["不存在的字符串abcxyz"])
    assert len(res_none) == 0
