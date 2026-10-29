import json
import pytest
from pathlib import Path
from zedhub import api
from zedhub.core.metadata_cache import MetadataCacheService
from zedhub.core.model import SearchRequest, Session, Thread
from zedhub.core.search import SearchService

def test_search_by_skill_parameter():
    threads = [
        Thread(id="t1", session_id="s1", agent_id="antigravity-acp", title="Planning UI", archived=False, projects=["/repo"])
    ]
    sessions = [
        Session(
            source_id="antigravity",
            external_id="s1",
            title="Planning UI",
            skills=["anatomy", "sh-user-skills"],
        ),
        Session(
            source_id="claude-code",
            external_id="s2",
            title="Refactor backend",
            skills=["sh-oop-refactor"],
        )
    ]

    svc = SearchService(threads=threads, sessions=sessions)

    # 1. 精准按 skill 参数过滤
    res = svc.search(SearchRequest(skill="anatomy"))
    assert res.total == 1
    assert res.hits[0].session_id == "s1"
    assert "anatomy" in res.hits[0].skills

    # 2. 过滤未命中的 skill
    res_none = svc.search(SearchRequest(skill="non-existent"))
    assert res_none.total == 0

    # 3. 通过 q="skill:sh-oop-refactor" 语法过滤
    res_syntax = svc.search(SearchRequest(q="skill:sh-oop-refactor"))
    assert res_syntax.total == 1
    assert res_syntax.hits[0].session_id == "s2"
    assert "skill" in res_syntax.hits[0].matched_fields

def test_sessions_skills_api(tmp_path, monkeypatch):
    # 模拟 Antigravity transcript
    tr = tmp_path / "brain" / "sess-123" / ".system_generated" / "logs" / "transcript.jsonl"
    tr.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "USER_INPUT", "created_at": "2026-10-28T10:00:00Z", "content": "anatomy：绘制电路图"},
        {
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-28T10:00:05Z",
            "tool_calls": [
                {
                    "name": "view_file",
                    "args": {"AbsolutePath": "/mock/skills/anatomy/SKILL.md"}
                }
            ]
        }
    ]
    with tr.open("w", encoding="utf-8") as f:
        for l in lines:
            f.write(json.dumps(l) + "\n")

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    ctx = api.CallContext()
    # 模拟 locate_session_files 返回该文件
    monkeypatch.setattr(
        "zedhub.core.agent_paths.locate_session_files",
        lambda src, sids, home=None: {"sess-123": [tr]}
    )

    result = api.call("sessions.skills", {"session_id": "sess-123", "source": "antigravity"}, ctx)
    assert result["session_id"] == "sess-123"
    assert "anatomy" in result["skills"]
    assert len(result["invocations"]) >= 1
    assert result["invocations"][0]["name"] == "anatomy"

def test_skills_list_summary(tmp_path):
    db_path = tmp_path / "cache.db"
    cache = MetadataCacheService(db_path=db_path)
    with cache._connect() as con:
        con.execute(
            """
            INSERT INTO cached_sessions (
                source_id, session_id, agent, title, skills_json
            ) VALUES
            ('antigravity', 's1', 'antigravity', 'Title 1', '["anatomy", "sh-user-skills"]'),
            ('claude-code', 's2', 'claude-code', 'Title 2', '["anatomy"]'),
            ('opencode', 's3', 'opencode', 'Title 3', '["legacy-skill"]')
            """
        )

    summary = cache.get_skills_summary()
    assert len(summary) == 3
    # 按调用频次降序，anatomy 应有 2 次
    assert summary[0]["name"] == "anatomy"
    assert summary[0]["count"] == 2
    # legacy-skill 在磁盘上不存在
    legacy_item = [x for x in summary if x["name"] == "legacy-skill"][0]
    assert legacy_item["count"] == 1
    assert legacy_item["exists_on_disk"] is False