from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from zedhub import logx


@pytest.fixture(autouse=True)
def clean_logging():
    yield
    logx.reset_logging()


def test_tee_writes_file_and_stream(tmp_path: Path) -> None:
    stream = io.StringIO()
    path = tmp_path / "daemon.log"

    logx.init_logging(path, stderr=stream)
    print("hello", file=sys.stderr)
    sys.stderr.flush()

    assert path.read_text(encoding="utf-8") == "hello\n"
    assert stream.getvalue() == "hello\n"


def test_rotation_keeps_one_backup(tmp_path: Path) -> None:
    path = tmp_path / "daemon.log"
    path.write_text("x" * (logx.MAX_LOG_BYTES + 1), encoding="utf-8")

    logx.init_logging(path, stderr=io.StringIO(), max_bytes=logx.MAX_LOG_BYTES)

    backup = tmp_path / ("daemon.log" + logx.LOG_BACKUP_SUFFIX)
    assert backup.is_file()
    assert backup.stat().st_size > logx.MAX_LOG_BYTES
    assert path.read_text(encoding="utf-8") == ""


def test_stderr_failure_does_not_block_file(tmp_path: Path) -> None:
    class Broken:
        def write(self, text: str) -> int:
            raise OSError("no console")

        def flush(self) -> None:
            raise OSError("no console")

    path = tmp_path / "daemon.log"
    logx.init_logging(path, stderr=Broken())

    print("still logged", file=sys.stderr)
    sys.stderr.flush()

    assert "still logged" in path.read_text(encoding="utf-8")


def test_reinit_switches_file_without_nesting(tmp_path: Path) -> None:
    first = tmp_path / "a.log"
    second = tmp_path / "b.log"

    logx.init_logging(first, stderr=io.StringIO())
    logx.init_logging(second, stderr=io.StringIO())
    print("once", file=sys.stderr)
    sys.stderr.flush()

    assert "once" in second.read_text(encoding="utf-8")
    assert first.read_text(encoding="utf-8") == ""
    assert logx.log_path() == second


def test_banner_writes_fields(tmp_path: Path) -> None:
    path = tmp_path / "daemon.log"
    logx.init_logging(path, stderr=io.StringIO())

    logx.banner(version="1.0.0", channel="dev", port=18766)
    sys.stderr.flush()

    text = path.read_text(encoding="utf-8")
    assert "[zedhub] version=1.0.0 channel=dev port=18766" in text
