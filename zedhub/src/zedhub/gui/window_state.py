"""窗口几何记忆：pywebview v6 不提供窗口状态持久化，自行落盘。

- 文件：``<通道数据根>/window.json``（dev/prod 自动隔离，无 BOM UTF-8）；
- 单位：Windows 下 pywebview 的 ``x/y/width/height`` 已是 DIP，原值存取；
- 兜底：尺寸过小/脏 JSON 回落默认；坐标不在任何显示器工作区时回落居中。
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ..channel import data_dir

WINDOW_STATE_FILE = "window.json"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 840
MIN_WIDTH = 480
MIN_HEIGHT = 360
MAX_DIMENSION = 16384

MONITOR_DEFAULTTONULL = 0


@dataclass
class WindowState:
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    x: int | None = None
    y: int | None = None
    maximized: bool = False


def window_state_path() -> Path:
    return data_dir() / WINDOW_STATE_FILE


def _as_int(value, default: int | None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def visible_on_screen(x: int, y: int, width: int, height: int) -> bool:
    """窗口中心是否落在任一显示器上（拔掉外接屏后旧坐标会不可见）。"""
    if sys.platform != "win32":
        return True

    class _Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [_Point, ctypes.c_ulong]
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        point = _Point(x + width // 2, y + min(40, height // 2))
        return bool(user32.MonitorFromPoint(point, MONITOR_DEFAULTTONULL))
    except (AttributeError, OSError):
        return True


def load_window_state() -> WindowState:
    try:
        raw = json.loads(window_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return WindowState()
    if not isinstance(raw, dict):
        return WindowState()

    width = _as_int(raw.get("width"), DEFAULT_WIDTH)
    height = _as_int(raw.get("height"), DEFAULT_HEIGHT)
    if width is None or not MIN_WIDTH <= width <= MAX_DIMENSION:
        width = DEFAULT_WIDTH
    if height is None or not MIN_HEIGHT <= height <= MAX_DIMENSION:
        height = DEFAULT_HEIGHT

    x = _as_int(raw.get("x"), None)
    y = _as_int(raw.get("y"), None)
    if x is None or y is None or not visible_on_screen(x, y, width, height):
        x = y = None

    return WindowState(
        width=width,
        height=height,
        x=x,
        y=y,
        maximized=bool(raw.get("maximized", False)),
    )


def save_window_state(state: WindowState) -> None:
    path = window_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "width": state.width,
        "height": state.height,
        "x": state.x,
        "y": state.y,
        "maximized": state.maximized,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
