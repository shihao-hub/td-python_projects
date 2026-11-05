"""批量归档 / 恢复测试：计划、写库、undo 清单与幂等语义。

测试用临时 Zed db 与临时数据根；不触碰真实 Zed / APPDATA。
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from zedhub.core import archiving


def _thread_blob(n: int) -> bytes:
    return uuid.UUID(int=n).bytes


def _make_zed_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE sidebar_threads ("
        " thread_id BLOB, session_id TEXT, agent_id TEXT, title TEXT,"
        " title_override TEXT, updated_at TEXT, created_at TEXT,"
        " folder_paths TEXT, archived INTEGER, interacted_at TEXT)"
    )
    rows = [
        (_thread_blob(1), "sid-1", "antigravity-acp", "会话一", None,
         "2026-10-01T10:00:00+00:00", "2026-10-01T09:00:00+00:00",
         "D:\\Users\\language_projects", 0, None),
        (_thread_blob(2), "sid-2", "antigravity-acp", "会话二", None,
         "2026-10-02T10:00:00+00:00", "2026-10-02T09:00:00+00:00",
         "D:\\Users\\language_projects", 0, None),
        (_thread_blob(3), "sid-3", "opencode", "别的 agent", None,
         "2026-10-03T10:00:00+00:00", "2026-10-03T09:00:00+00:00",
         "D:\\Users\\language_projects", 0, None),
        (_thread_blob(4), "sid-4", "antigravity-acp", "已归档", None,
         "2026-10-04T10:00:00+00:00", "2026-10-04T09:00:00+00:00",
         "D:\\Users\\language_projects", 1, None),
        (_thread_blob(5), "sid-5", "antigravity-acp", "别的目录", None,
         "2026-10-05T10:00:00+00:00", "2026-10-05T09:00:00+00:00",
         "D:\\Users\\other", 0, None),
    ]
    con.executemany(
        "INSERT INTO sidebar_threads VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch):
    """把数据根与进程检查都指向临时环境。"""
    data = tmp_path / "data"
    data.mkdir()

    def fake_subdir(name: str) -> Path:
        d = data / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr("zedhub.core.snapshot.data_subdir", fake_subdir)
    monkeypatch.setattr("zedhub.core.backup.data_subdir", fake_subdir)
    monkeypatch.setattr("zedhub.core.journal.data_subdir", fake_subdir)
    monkeypatch.setattr("zedhub.core.archiving.data_subdir", fake_subdir)
    monkeypatch.setattr("zedhub.core.archiving.find_running", lambda *a, **k: [])

    zed = tmp_path / "zed" / "db.sqlite"
    zed.parent.mkdir()
    _make_zed_db(zed)
    return zed, data


def _archived(zed: Path) -> dict[str, int]:
    con = sqlite3.connect(f"file:{zed}?mode=ro", uri=True)
    try:
        return {
            str(uuid.UUID(bytes=r[0])): int(r[1] or 0)
            for r in con.execute("SELECT thread_id, archived FROM sidebar_threads")
        }
    finally:
        con.close()


def test_plan_archive_only_matches_agent_project_and_active(sandbox) -> None:
    zed, _ = sandbox
    plan = archiving.plan_archive(agent="antigravity-acp",
                                  project="D:\\Users\\language_projects",
                                  exact=True, zed_db=zed)
    titles = sorted(t.title for t in plan.threads)
    assert titles == ["会话一", "会话二"]  # 已归档/别的 agent/别的目录都不算


def test_execute_archive_writes_undo_and_updates_rows(sandbox) -> None:
    zed, data = sandbox
    plan = archiving.plan_archive(agent="antigravity-acp",
                                  project="D:\\Users\\language_projects",
                                  exact=True, zed_db=zed)
    result = archiving.execute_archive(plan, zed_db=zed)
    assert result["applied"] is True
    assert result["count"] == 2
    assert result["verify"] == "ok"

    states = _archived(zed)
    assert states[str(uuid.UUID(int=1))] == 1
    assert states[str(uuid.UUID(int=2))] == 1
    assert states[str(uuid.UUID(int=3))] == 0  # 别的 agent 未被误伤

    undo = Path(result["undo_file"])
    assert undo.is_file()
    payload = archiving.read_undo(result["operation_id"])
    assert payload is not None and payload["count"] == 2
    assert payload["restored_at"] is None


def test_restore_uses_exact_undo_list(sandbox) -> None:
    zed, _ = sandbox
    plan = archiving.plan_archive(agent="antigravity-acp",
                                  project="D:\\Users\\language_projects",
                                  exact=True, zed_db=zed)
    archived = archiving.execute_archive(plan, zed_db=zed)

    restore_plan = archiving.plan_restore(zed_db=zed)
    assert len(restore_plan.threads) == 2
    restored = archiving.execute_archive(restore_plan, zed_db=zed)
    assert restored["count"] == 2

    states = _archived(zed)
    assert states[str(uuid.UUID(int=1))] == 0
    assert states[str(uuid.UUID(int=2))] == 0
    # 恢复后 undo 清单标记 restored_at
    payload = archiving.read_undo(archived["operation_id"])
    assert payload and payload["restored_at"]


def test_restore_twice_is_rejected(sandbox) -> None:
    zed, _ = sandbox
    plan = archiving.plan_archive(agent="antigravity-acp",
                                  project="D:\\Users\\language_projects",
                                  exact=True, zed_db=zed)
    archiving.execute_archive(plan, zed_db=zed)
    archiving.execute_archive(archiving.plan_restore(zed_db=zed), zed_db=zed)
    with pytest.raises(ValueError):
        archiving.plan_restore(zed_db=zed)


def test_list_undos_reports_operations(sandbox) -> None:
    zed, _ = sandbox
    plan = archiving.plan_archive(agent="antigravity-acp",
                                  project="D:\\Users\\language_projects",
                                  exact=True, zed_db=zed)
    archiving.execute_archive(plan, zed_db=zed)
    ops = archiving.list_undos()
    assert len(ops) == 1
    assert ops[0]["agent"] == "antigravity-acp"
    assert ops[0]["count"] == 2


def test_dry_run_does_not_write(sandbox) -> None:
    zed, _ = sandbox
    result = archiving.run_archive(
        agent="antigravity-acp", project="D:\\Users\\language_projects",
        exact=True, apply=False, zed_db=zed,
    )
    assert result["applied"] is False
    assert result["count"] == 2
    assert _archived(zed)[str(uuid.UUID(int=1))] == 0
