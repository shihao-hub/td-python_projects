from __future__ import annotations

from zedhub.core.model import SearchRequest, Session, SessionScope, Thread
from zedhub.core.search import SearchService


def test_all_scope_includes_external_session() -> None:
    session = Session(
        source_id="codex",
        external_id="codex-1",
        title="终端会话",
        agent="codex",
        directory="C:/work",
    )

    result = SearchService(threads=[], sessions=[session]).search(
        SearchRequest(scope=SessionScope.ALL)
    )

    assert result.total == 1
    assert result.hits[0].kind == "external_session"
    assert result.hits[0].management == "external"
    assert result.hits[0].source_id == "codex"


def test_scope_filters_zed_and_external() -> None:
    thread = Thread(
        id="thread-1",
        session_id=None,
        agent_id="claude-acp",
        title="Zed 会话",
        archived=False,
        projects=[],
    )
    external = Session(
        source_id="claude-code",
        external_id="claude-1",
        title="终端会话",
        agent="claude-code",
    )
    service = SearchService(threads=[thread], sessions=[external])

    assert service.search(SearchRequest(scope=SessionScope.ZED)).total == 1
    assert service.search(SearchRequest(scope=SessionScope.EXTERNAL)).total == 1
