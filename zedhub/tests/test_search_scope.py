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


def test_search_project_normalization_slashes() -> None:
    thread = Thread(
        id="thread-win",
        session_id=None,
        agent_id="claude-acp",
        title="Windows 反斜杠项目",
        archived=False,
        projects=["D:\\Users\\language_projects"],
    )
    external = Session(
        source_id="claude-code",
        external_id="claude-posix",
        title="正斜杠项目",
        agent="claude-code",
        directory="D:/Users/language_projects",
    )
    service = SearchService(threads=[thread], sessions=[external])

    # 使用正斜杠筛选，两者均能命中
    res_slash = service.search(
        SearchRequest(scope=SessionScope.ALL, project="D:/Users/language_projects")
    )
    assert res_slash.total == 2

    # 使用反斜杠筛选，两者均能命中
    res_backslash = service.search(
        SearchRequest(scope=SessionScope.ALL, project="D:\\Users\\language_projects")
    )
    assert res_backslash.total == 2

    # 大小写不敏感且带尾部斜杠测试
    res_case = service.search(
        SearchRequest(scope=SessionScope.ALL, project="d:/users/language_projects/")
    )
    assert res_case.total == 2


def test_external_opencode_agent_normalized_and_mode_passed() -> None:
    session = Session(
        source_id="opencode",
        external_id="ses-123",
        title="写代码会话",
        agent="build",  # 内部模式
        directory="D:/Users/project",
    )
    service = SearchService(threads=[], sessions=[session])

    res = service.search(SearchRequest(scope=SessionScope.EXTERNAL))
    assert res.total == 1
    hit = res.hits[0]
    assert hit.agent_id == "opencode"  # 顶层归一化为 opencode
    assert hit.mode == "build"          # 内部模式保存在 mode 字段
    assert hit.management == "external"

    # 按 agent="opencode" 能够正常查出
    res_filtered = service.search(SearchRequest(scope=SessionScope.EXTERNAL, agent="opencode"))
    assert res_filtered.total == 1


def test_zed_linked_opencode_mode_passed() -> None:
    thread = Thread(
        id="thread-oc",
        session_id="ses-oc",
        agent_id="opencode",
        title="Zed OpenCode 会话",
        archived=False,
        projects=[],
    )
    session = Session(
        source_id="opencode",
        external_id="ses-oc",
        title="Zed OpenCode 会话",
        agent="plan",
    )
    service = SearchService(threads=[thread], sessions=[session])

    res = service.search(SearchRequest(scope=SessionScope.ZED))
    assert res.total == 1
    hit = res.hits[0]
    assert hit.agent_id == "opencode"
    assert hit.mode == "plan"
    assert hit.management == "zed"
