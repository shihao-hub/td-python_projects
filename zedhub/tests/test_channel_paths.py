from __future__ import annotations

from pathlib import Path

import pytest

from zedhub import channel, contract
from zedhub.client import DaemonClient
from zedhub.core.errors import DaemonUnreachableError


@pytest.fixture
def appdata(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv(channel.CHANNEL_ENV, raising=False)
    return tmp_path


def _legacy_root(appdata: Path) -> Path:
    return appdata / "language_projects" / "zedhub"


def test_explicit_channel_override(appdata: Path, monkeypatch) -> None:
    monkeypatch.setenv(channel.CHANNEL_ENV, "prod")
    assert channel.channel() == "prod"
    monkeypatch.setenv(channel.CHANNEL_ENV, "dev")
    assert channel.channel() == "dev"


def test_invalid_channel_override_falls_back(appdata: Path, monkeypatch) -> None:
    monkeypatch.setenv(channel.CHANNEL_ENV, "staging")
    assert channel.channel() in channel.CHANNELS


def test_data_dir_isolated_per_channel(appdata: Path, monkeypatch) -> None:
    monkeypatch.setenv(channel.CHANNEL_ENV, "dev")
    dev = channel.data_dir()
    monkeypatch.setenv(channel.CHANNEL_ENV, "prod")
    prod = channel.data_dir()

    root = _legacy_root(appdata)
    assert dev == root / "dev"
    assert prod == root / "prod"
    assert dev.is_dir() and prod.is_dir()
    assert channel.runtime_dir() == prod / "runtime"


def test_migration_copies_only_index_and_operations(appdata: Path, monkeypatch) -> None:
    legacy = _legacy_root(appdata)
    (legacy / "index").mkdir(parents=True)
    (legacy / "index" / "content_search.db").write_bytes(b"idx")
    (legacy / "operations").mkdir()
    (legacy / "operations" / "op.json").write_text("{}", encoding="utf-8")
    (legacy / "backups").mkdir()
    (legacy / "backups" / "big.db").write_bytes(b"x")
    (legacy / "runtime").mkdir()
    (legacy / "runtime" / "daemon.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv(channel.CHANNEL_ENV, "dev")
    target = channel.data_dir()

    assert (target / "index" / "content_search.db").read_bytes() == b"idx"
    assert (target / "operations" / "op.json").is_file()
    assert not (target / "backups").exists()
    assert not (target / "runtime" / "daemon.json").exists()
    # 旧文件保留不删
    assert (legacy / "index" / "content_search.db").is_file()
    assert (legacy / "backups" / "big.db").is_file()
    assert (legacy / channel.MIGRATION_MARKER).is_file()


def test_migration_runs_only_once(appdata: Path, monkeypatch) -> None:
    legacy = _legacy_root(appdata)
    (legacy / "index").mkdir(parents=True)
    (legacy / "index" / "a.db").write_bytes(b"1")

    monkeypatch.setenv(channel.CHANNEL_ENV, "dev")
    target = channel.data_dir()
    assert (target / "index" / "a.db").is_file()

    # marker 落定后新增的旧数据不再复制（避免每次启动重复搬运）
    (legacy / "index" / "b.db").write_bytes(b"2")
    channel.data_dir()
    assert not (target / "index" / "b.db").exists()


def test_default_ports_follow_channel(appdata: Path, monkeypatch) -> None:
    monkeypatch.setenv(channel.CHANNEL_ENV, "prod")
    assert contract.default_http_port() == contract.DEFAULT_HTTP_PORT
    assert contract.default_ws_port() == contract.DEFAULT_WS_PORT

    monkeypatch.setenv(channel.CHANNEL_ENV, "dev")
    assert contract.default_http_port() == 0
    assert contract.default_ws_port() == 0


def test_dev_client_without_address_reports_not_running(appdata: Path, monkeypatch) -> None:
    monkeypatch.setenv(channel.CHANNEL_ENV, "dev")
    with pytest.raises(DaemonUnreachableError, match="dev 通道"):
        DaemonClient(None).ensure_connected()


def test_dev_client_reads_channel_address_file(appdata: Path, monkeypatch) -> None:
    monkeypatch.setenv(channel.CHANNEL_ENV, "dev")
    runtime = channel.runtime_dir()
    (runtime / "daemon.json").write_text(
        '{"host": "127.0.0.1", "port": 18766, "ws_port": 18765}', encoding="utf-8"
    )
    host, port, source = DaemonClient(None).resolve()
    assert (host, port, source) == ("127.0.0.1", 18766, "address_file")
