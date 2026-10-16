from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from zedhub import api
from zedhub.core.index import ContentIndexService
from zedhub.core.model import Message, MessagePart, SearchRequest, Session, SessionContent, Thread
from zedhub.core.search import SearchService


def test_index_build_and_search_flow(tmp_path: Path) -> None:
    db_file = tmp_path / "test_index_e2e.db"
    index_service = ContentIndexService(db_file)

    session = Session(
        source_id="antigravity",
        external_id="conv-101",
        title="测试会话",
        agent="antigravity",
        directory="D:/workspace",
    )
    content = SessionContent(
        session=session,
        messages=[
            Message(
                id="m1",
                role="user",
                parts=[MessagePart(id="p1", type="text", text="请帮我写一个基于 FastAPI 的后端服务")],
            ),
            Message(
                id="m2",
                role="assistant",
                parts=[MessagePart(id="p2", type="text", text="好的，我们可以使用 FastAPI 和 SQLite FTS5 来实现全文检索")],
            ),
        ],
    )

    mock_source = MagicMock()
    mock_source.list_sessions.return_value = [session]
    mock_source.get_content.return_value = content

    with patch("zedhub.core.index.get_source", return_value=mock_source):
        # 1. 首次构建
        stats = index_service.build_index(sources=["antigravity"])
        assert stats.scanned_sessions == 1
        assert stats.updated_sessions == 1
        assert stats.skipped_sessions == 0
        assert stats.indexed_messages == 2

        # 2. 状态查询
        status = index_service.get_status()
        assert status.indexed_sessions == 1
        assert status.indexed_messages == 2
        assert status.db_size_bytes > 0

        # 3. 再次增量构建（无变化则跳过）
        stats2 = index_service.build_index(sources=["antigravity"])
        assert stats2.scanned_sessions == 1
        assert stats2.updated_sessions == 0
        assert stats2.skipped_sessions == 1

        # 4. SearchService 正文联合检索
        search_svc = SearchService(
            threads=[],
            sessions=[session],
            index_service=index_service,
        )

        # 搜标题中不存在但正文存在的词 "FastAPI"
        res = search_svc.search(SearchRequest(q="FastAPI", content=True))
        assert res.total == 1
        hit = res.hits[0]
        assert hit.session_id == "conv-101"
        assert "content" in hit.matched_fields
        assert len(hit.snippets) > 0
        assert "FastAPI" in hit.snippets[0].text

        # 搜中文词 "全文检索"
        res_cn = search_svc.search(SearchRequest(q="全文检索", content=True))
        assert res_cn.total == 1
        assert "content" in res_cn.hits[0].matched_fields

        # content=False 时不搜正文
        res_off = search_svc.search(SearchRequest(q="全文检索", content=False))
        assert res_off.total == 0


def test_api_index_endpoints(tmp_path: Path) -> None:
    db_file = tmp_path / "test_api_index.db"
    ctx = api.CallContext(zed_db=None, opencode_db=None)

    with patch("zedhub.core.paths.index_db_path", return_value=db_file):
        # 初始 status
        st = api.call("index.status", {}, ctx)
        assert st["indexed_sessions"] == 0

        # 触发 build
        with patch("zedhub.core.index.session_source_ids", return_value=[]):
            b_res = api.call("index.build", {"rebuild": True}, ctx)
            assert b_res["scanned_sessions"] == 0
