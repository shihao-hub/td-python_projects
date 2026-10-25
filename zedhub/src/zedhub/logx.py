"""落盘日志：daemon 全通道可观测性（对应 go-wails SOP Step 4c）。

- 单一文件 ``<dataDir>/runtime/daemon.log``，启动时轮转（2 MB × 1 份 .1）；
- tee：文件与 stderr 双写；stderr 写失败（无控制台 / pythonw）绝不阻断
  文件落盘——文件是唯一可靠通道；
- 幂等：重复 ``init_logging`` 只替换文件句柄，不叠加 tee；
- ``install_excepthook``：未捕获异常写完整栈并提示日志路径；
- 安全：日志不记录可能含令牌的完整命令行、注册表值内容。
"""

from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

MAX_LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUP_SUFFIX = ".1"

_state: dict[str, object] = {
    "path": None,
    "file": None,
    "tee": None,
    "original": None,
    "restore": None,
}


class _Tee(io.TextIOBase):
    """文件优先、stderr 容错的双写流（GUI/无控制台场景下 stderr 可失败）。"""

    def __init__(self, original, file) -> None:
        self._original = original
        self._file = file

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, text) -> int:
        if not isinstance(text, str):
            text = str(text)
        if self._file is not None:
            try:
                self._file.write(text)
                self._file.flush()
            except (OSError, ValueError):
                pass
        if self._original is not None:
            try:
                self._original.write(text)
                self._original.flush()
            except (OSError, ValueError, AttributeError):
                pass
        return len(text)

    def flush(self) -> None:
        for stream in (self._file, self._original):
            if stream is None:
                continue
            try:
                stream.flush()
            except (OSError, ValueError, AttributeError):
                pass

    def fileno(self) -> int:
        if self._original is not None and hasattr(self._original, "fileno"):
            return self._original.fileno()
        raise OSError("log stream has no fileno")


def _rotate(path: Path, max_bytes: int) -> None:
    """启动时轮转：超过上限就把当前文件改成 .1（覆盖旧备份）。"""
    try:
        if path.stat().st_size <= max_bytes:
            return
    except OSError:
        return
    backup = path.parent / (path.name + LOG_BACKUP_SUFFIX)
    try:
        backup.unlink(missing_ok=True)
        path.replace(backup)
    except OSError:
        pass


def init_logging(
    path: Path,
    *,
    stderr=None,
    max_bytes: int = MAX_LOG_BYTES,
) -> Path:
    """打开日志文件并把 ``sys.stderr`` 换成文件优先的 tee；返回日志路径。"""
    global _state
    previous_file = _state.get("file")
    if previous_file is not None:
        try:
            previous_file.close()
        except OSError:
            pass
    if _state.get("restore") is None:
        _state["restore"] = sys.stderr
    original = _state.get("original")
    if original is None:
        original = stderr if stderr is not None else sys.stderr
        _state["original"] = original

    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate(path, max_bytes)
    file = open(path, "a", encoding="utf-8", errors="replace", newline="")
    tee = _Tee(original, file)
    _state.update(path=path, file=file, tee=tee)
    sys.stderr = tee
    return path


def log_path() -> Path | None:
    """当前日志文件路径（未初始化返回 None）。"""
    value = _state.get("path")
    return value if isinstance(value, Path) else None


def banner(**fields) -> None:
    """启动横幅：`[zedhub] key=value ...`，排障第一手信息。"""
    parts = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[zedhub] {parts}", file=sys.stderr, flush=True)


def install_excepthook() -> None:
    """未捕获异常：完整栈落盘 + 日志路径提示（不重复调用原 hook）。"""
    if _state.get("hook") is not None:
        return

    def _hook(exc_type, exc, tb) -> None:
        try:
            traceback.print_exception(exc_type, exc, tb, file=sys.stderr)
            current = log_path()
            if current is not None:
                print(f"zedhub: crash log -> {current}", file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001 — 崩溃路径禁止二次失败
            pass

    sys.excepthook = _hook
    _state["hook"] = _hook


def reset_logging() -> None:
    """恢复原始 stderr 并关闭文件（测试与长时间宿主进程收尾用）。"""
    global _state
    restore = _state.get("restore")
    if restore is not None:
        sys.stderr = restore
    file = _state.get("file")
    if file is not None:
        try:
            file.close()
        except OSError:
            pass
    _state = {
        "path": None,
        "file": None,
        "tee": None,
        "original": None,
        "restore": None,
    }
