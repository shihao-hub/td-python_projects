"""notifier / scheduler / applog 测试：monkeypatch subprocess.run，不实发不实装。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from todo_notify.applog import get_log_dir
from todo_notify.notifier import (
    RECIPIENT_OPEN_ID,
    NotifyError,
    build_command,
    build_post_content,
    send_lark,
)
from todo_notify.scheduler import SCHEDULES, SchedulerError, install, uninstall

FAKE_LARK_CLI = "C:/fake/lark-cli.cmd"


@pytest.fixture(autouse=True)
def _fake_lark_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """notifier 测试统一伪造 lark-cli 路径，不依赖真实安装。"""
    monkeypatch.setattr(shutil, "which", lambda name: FAKE_LARK_CLI if name == "lark-cli" else None)


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------- notifier ----------


def test_build_post_content_escapes_newlines() -> None:
    """post JSON 单行、换行为转义序列、首行作标题——规避 cmd.exe 换行截断。"""
    content = build_post_content("标题行\n\n正文一\n正文二")
    assert "\n" not in content  # 真实换行不得出现在命令行参数里
    payload = json.loads(content)
    block = payload["zh_cn"]
    assert block["title"] == "标题行"
    assert block["content"][0][0]["tag"] == "md"
    assert block["content"][0][0]["text"] == "标题行\n\n正文一\n正文二"


def test_build_post_content_ascii_only() -> None:
    """ensure_ascii 保证命令行参数纯 ASCII，规避 cmd.exe 特殊字符问题。"""
    content = build_post_content("📋 待办提醒\n\n中文【内容】⚠️")
    assert content.isascii()


def test_build_command_arguments() -> None:
    """命令参数完整：which 解析的完整路径、bot 身份、post JSON 载荷。"""
    cmd = build_command("你好\n\n正文")
    assert cmd[0] == FAKE_LARK_CLI
    assert cmd[1:6] == ["im", "+messages-send", "--as", "bot", "--user-id"]
    assert cmd[6] == RECIPIENT_OPEN_ID
    assert cmd[7:10] == ["--msg-type", "post", "--content"]
    payload = json.loads(cmd[10])
    assert payload["zh_cn"]["title"] == "你好"
    assert payload["zh_cn"]["content"][0][0]["text"] == "你好\n\n正文"


def test_build_command_lark_cli_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """PATH 中无 lark-cli → NotifyError。"""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(NotifyError, match="未找到 lark-cli"):
        build_command("你好")


def test_send_lark_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """returncode 0 且 stdout JSON 含 message_id → 返回该 id。"""
    calls: list[list[str]] = []
    kwargs_seen: dict = {}

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        kwargs_seen.update(kwargs)
        return _completed(stdout='{"message_id": "om_123"}')

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert send_lark("hello") == "om_123"
    assert calls[0][0] == FAKE_LARK_CLI
    assert kwargs_seen.get("encoding") == "utf-8"


def test_send_lark_nested_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """message_id 嵌在 data 字段时也能取到。"""
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: _completed(stdout='{"data": {"message_id": "om_456"}}')
    )
    assert send_lark("hello") == "om_456"


def test_send_lark_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """lark-cli 非零退出 → NotifyError。"""
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _completed(1, stderr="boom"))
    with pytest.raises(NotifyError, match="退出码 1"):
        send_lark("hello")


def test_send_lark_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """stdout 非 JSON → NotifyError。"""
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _completed(stdout="not json"))
    with pytest.raises(NotifyError, match="无法解析"):
        send_lark("hello")


def test_send_lark_missing_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """响应缺 message_id → NotifyError。"""
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _completed(stdout="{}"))
    with pytest.raises(NotifyError, match="message_id"):
        send_lark("hello")


# ---------- scheduler ----------


@pytest.fixture
def fake_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """伪造一个含 todonotify.exe 的 venv Scripts 目录。"""
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "todonotify.exe").touch()
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    return scripts


def test_install_builds_schtasks_commands(
    fake_venv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install 构造两条 DAILY 任务命令，/TR 指向 venv 内 exe 且带 --notify。"""
    cmds: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (cmds.append(cmd), _completed())[1])
    created = install()
    assert created == list(SCHEDULES)
    by_name = {cmd[cmd.index("/TN") + 1]: cmd for cmd in cmds}
    assert set(by_name) == {"todo_notify_morning", "todo_notify_evening"}
    morning = by_name["todo_notify_morning"]
    assert morning[0] == "schtasks"
    assert morning[morning.index("/SC") + 1] == "DAILY"
    assert morning[morning.index("/ST") + 1] == "09:00"
    assert morning[morning.index("/TR") + 1] == str(fake_venv / "todonotify.exe") + " --notify"
    assert "/F" in morning
    assert by_name["todo_notify_evening"][by_name["todo_notify_evening"].index("/ST") + 1] == "20:00"


def test_install_without_exe_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """venv 内无 todonotify.exe → SchedulerError，提示 uv sync。"""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    with pytest.raises(SchedulerError, match="uv sync"):
        install()


def test_install_schtasks_failure(
    fake_venv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """schtasks 非零退出 → SchedulerError。"""
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _completed(1, stderr="拒绝访问"))
    with pytest.raises(SchedulerError, match="拒绝访问"):
        install()


def test_uninstall_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务不存在（Query 非零）→ 幂等返回空列表，不调 Delete。"""
    cmds: list[list[str]] = []

    def fake_run(cmd, **kw):
        cmds.append(cmd)
        return _completed(1)  # Query 查不到

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert uninstall() == []
    assert all("/Delete" not in c for c in cmds)


def test_uninstall_removes_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务存在 → Query 与 Delete 均被调用，返回任务名。"""
    cmds: list[list[str]] = []

    def fake_run(cmd, **kw):
        cmds.append(cmd)
        return _completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert uninstall() == list(SCHEDULES)
    deletes = [c for c in cmds if "/Delete" in c]
    assert len(deletes) == 2
    assert all("/F" in c for c in deletes)
    assert all(c[c.index("/TN") + 1] in SCHEDULES for c in deletes)


# ---------- applog ----------


def test_log_dir_creates_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """APPDATA 可用时日志目录链自动创建（含 language_projects 一层）。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    log_dir = get_log_dir()
    assert log_dir == tmp_path / "language_projects" / "todo_notify"
    assert log_dir.is_dir()


def test_log_dir_fallback_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """取不到 APPDATA 时回退 ~/.language_projects/todo_notify/。"""
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    log_dir = get_log_dir()
    assert log_dir == tmp_path / ".language_projects" / "todo_notify"
    assert log_dir.is_dir()
