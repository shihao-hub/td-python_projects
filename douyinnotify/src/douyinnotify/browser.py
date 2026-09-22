"""无头 Chrome 进程与裸 CDP（websocket）连接生命周期管理。

不引入 Playwright 等重量驱动：启动系统 Chrome
`--headless=new --remote-debugging-port=0 --user-data-dir=<专用 profile>`，
经 profile 目录下 DevToolsActivePort 文件发现端口，建 browser 级 websocket
连接（flatten 模式按 sessionId 路由页面域消息）。

结束必须杀整棵进程树（taskkill /T /F），防止残留 chrome.exe。
全程不与页面交互（忽略登录弹窗、不点任何按钮）。
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import websocket

# Chrome 常见安装位置（含用户级安装）
_DEFAULT_CHROME_PATHS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")),
)


class BrowserError(RuntimeError):
    """Chrome 启动 / CDP 通信失败。"""


def find_chrome() -> Path:
    """定位系统 Chrome，可用环境变量 DOUYINNOTIFY_CHROME 覆盖。"""
    override = os.environ.get("DOUYINNOTIFY_CHROME")
    candidates: tuple[Path, ...] = _DEFAULT_CHROME_PATHS
    if override:
        candidates = (Path(override),) + _DEFAULT_CHROME_PATHS
    for path in candidates:
        if path.is_file():
            return path
    raise BrowserError(
        "未找到 Chrome，请安装或设置环境变量 DOUYINNOTIFY_CHROME 指向 chrome.exe"
    )


class CDPConnection:
    """browser 级 CDP websocket 连接（flatten 模式）。"""

    def __init__(self, ws_url: str, connect_timeout: float = 10.0) -> None:
        self._ws = websocket.create_connection(
            ws_url,
            timeout=connect_timeout,
            suppress_origin=True,
        )
        self._ids = itertools.count(1)
        self._events: list[dict[str, Any]] = []

    def close(self) -> None:
        """关闭 websocket（连接失败不抛错，进程树另有清理）。"""
        with contextlib.suppress(OSError):
            self._ws.close()

    def _recv(self, deadline: float) -> dict[str, Any] | None:
        """在 deadline 前收一条消息；超时返回 None。"""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        self._ws.settimeout(remaining)
        try:
            raw = self._ws.recv()
        except (websocket.WebSocketTimeoutException, TimeoutError, OSError):
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return message if isinstance(message, dict) else None

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """发送一条 CDP 命令并等待其响应；期间收到的事件入缓冲。"""
        message: dict[str, Any] = {
            "id": next(self._ids),
            "method": method,
            "params": params or {},
        }
        if session_id:
            message["sessionId"] = session_id
        self._ws.send(json.dumps(message))

        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline)
            if msg is None:
                raise BrowserError(f"CDP 调用超时: {method}")
            if msg.get("id") == message["id"]:
                if "error" in msg:
                    raise BrowserError(f"CDP 错误 {method}: {msg['error']}")
                result = msg.get("result", {})
                return result if isinstance(result, dict) else {}
            if "method" in msg:
                self._events.append(msg)

    def wait_any_event(
        self,
        methods: set[str],
        timeout: float,
    ) -> dict[str, Any] | None:
        """等待任一指定方法的事件；先查缓冲再阻塞收取，超时返回 None。"""
        for index, msg in enumerate(self._events):
            if msg.get("method") in methods:
                return self._events.pop(index)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._recv(deadline)
            if msg is None:
                return None
            if msg.get("method") in methods:
                return msg
            if "method" in msg:
                self._events.append(msg)
        return None

    def drain_event(self, method: str, timeout: float) -> dict[str, Any] | None:
        """等待单个指定方法的事件（wait_any_event 的单事件特例）。"""
        return self.wait_any_event({method}, timeout)


class ChromeProcess:
    """一个无头 Chrome 进程 + 其 CDP 连接入口。"""

    def __init__(self, process: subprocess.Popen[bytes], ws_url: str) -> None:
        self.process = process
        self.ws_url = ws_url
        self._connection: CDPConnection | None = None

    def connect(self) -> CDPConnection:
        """建立 CDP 连接（同一进程只建一次）。"""
        if self._connection is None:
            self._connection = CDPConnection(self.ws_url)
        return self._connection

    def kill_tree(self) -> None:
        """杀掉整棵 Chrome 进程树（headless chrome 会派生多个子进程）。"""
        if self._connection is not None:
            self._connection.close()
        if self.process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )


def _wait_devtools_port(port_file: Path, timeout: float = 15.0) -> str:
    """轮询 DevToolsActivePort 文件，返回 browser 级 websocket URL。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_file.exists():
            lines = port_file.read_text(encoding="utf-8").split()
            if len(lines) >= 2:
                return f"ws://127.0.0.1:{lines[0]}{lines[1]}"
        time.sleep(0.1)
    raise BrowserError(f"等待 DevToolsActivePort 超时（{timeout:.0f}s）")


@contextlib.contextmanager
def launch_chrome(profile_dir: Path) -> Iterator[ChromeProcess]:
    """启动一个无头 Chrome 会话，退出时无论成败都杀掉进程树。"""
    chrome_path = find_chrome()
    profile_dir.mkdir(parents=True, exist_ok=True)
    port_file = profile_dir / "DevToolsActivePort"
    port_file.unlink(missing_ok=True)

    process: subprocess.Popen[bytes] = subprocess.Popen(
        [
            str(chrome_path),
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--window-size=1280,900",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,  # 杜绝任何 conhost 闪现
    )
    chrome = ChromeProcess(process, "")
    try:
        chrome.ws_url = _wait_devtools_port(port_file)
        yield chrome
    finally:
        chrome.kill_tree()
