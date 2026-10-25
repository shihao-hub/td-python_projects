"""GUI 壳：pywebview 窗口 + 托盘 + 内嵌 daemon（go-wails 双通道的 Python 实现）。

- 业务仍在 daemon：壳内嵌同一 ``serve_with_lifecycle``，窗口只是 ``/ui`` 的
  宿主，不 import ``core``、不直连数据库；
- 已有 CLI ``serve`` 在跑时复用其地址，不再另起 daemon；
- **关窗销毁窗口**（释放 WebView2 子进程 + ``gc.collect()``，对齐 go-wails 的
  WindowClosing 放行与 FreeOSMemory），托盘常驻；托盘「显示窗口」重建窗口；
- 图标按构建形态区分：dev 用 Python 双蛇，release 用专属三环图标；
- 窗口几何落 ``window.json``（防抖保存，最大化不污染还原尺寸）。
"""

from __future__ import annotations

import ctypes
import gc
import sys
import threading
import time
import webbrowser
from pathlib import Path

import webview

from .. import logx
from ..buildid import build_id, is_production_build, package_version
from ..channel import channel, data_dir, runtime_dir
from ..contract import DEFAULT_HTTP_HOST
from ..lifecycle import InstanceLock, probe, read_address
from .window_state import (
    MIN_HEIGHT,
    MIN_WIDTH,
    WindowState,
    load_window_state,
    save_window_state,
    visible_on_screen,
)

APP_NAME = "ZedHub"
GUI_TITLE = "ZedHub 会话面板"
ASSETS = Path(__file__).parent / "assets"
ICON_RELEASE = ASSETS / "icon.ico"
ICON_DEV_BUNDLED = ASSETS / "icon-dev.ico"
PYTHON_ICON_RELATIVE = Path("docs") / "assets" / "projects" / "python_projects" / "python-default.ico"

GEOMETRY_DEBOUNCE_S = 0.7
DAEMON_READY_TIMEOUT_S = 30.0
BACKGROUND_COLOR = "#0d0f13"


def _dev_python_icon() -> Path | None:
    """dev 通道的 Python 双蛇图标：打包内副本优先，源码态回退父仓资产。"""
    if ICON_DEV_BUNDLED.is_file():
        return ICON_DEV_BUNDLED
    try:
        repo_root = Path(__file__).resolve().parents[5]
    except IndexError:
        return None
    candidate = repo_root / PYTHON_ICON_RELATIVE
    return candidate if candidate.is_file() else None


def app_icon() -> Path:
    """dev 用 Python 双蛇图标，release 用专属三环图标。"""
    if not is_production_build():
        python_icon = _dev_python_icon()
        if python_icon is not None:
            return python_icon
    return ICON_RELEASE


def _message_box(text: str, title: str = APP_NAME) -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
            return
        except OSError:
            pass
    print(f"{title}: {text}", file=sys.stderr)


def _existing_daemon_url() -> str | None:
    """已有 daemon（CLI serve / 上一个 GUI）存活时复用其地址。"""
    info = read_address()
    if not info:
        return None
    host = str(info.get("host", DEFAULT_HTTP_HOST))
    port = int(info.get("port", 0) or 0)
    if port <= 0 or probe(host, port) is None:
        return None
    return f"http://{host}:{port}/ui"


class DaemonHost:
    """内嵌 daemon：已有实例则复用，否则后台线程跑同一 serve 生命周期。"""

    def __init__(self) -> None:
        self.server = None
        self.url = ""
        self.error: Exception | None = None
        self.external = False
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        existing = _existing_daemon_url()
        if existing:
            self.external = True
            self.url = existing
            self._ready.set()
            return
        self._thread = threading.Thread(target=self._run, name="zedhub-daemon", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from ..lifecycle import serve_with_lifecycle

        try:
            serve_with_lifecycle(
                host=DEFAULT_HTTP_HOST,
                port=None,
                ws_port=None,
                on_ready=self._on_ready,
            )
        except Exception as exc:  # noqa: BLE001 — 线程边界，错误转交主线程
            self.error = exc
            self._ready.set()

    def _on_ready(self, server, http_port: int, ws_port: int) -> None:
        self.server = server
        self.url = f"http://{DEFAULT_HTTP_HOST}:{http_port}/ui"
        self._ready.set()
        logx.banner(
            gui="daemon-ready", url=self.url, http_port=http_port, ws_port=ws_port
        )

    def wait(self, timeout: float = DAEMON_READY_TIMEOUT_S) -> str:
        if not self._ready.wait(timeout):
            raise RuntimeError(f"daemon 启动超时（{timeout:.0f}s）")
        if self.error is not None:
            raise self.error
        info = read_address()
        if not info:
            raise RuntimeError("daemon 未写入地址文件")
        host = str(info.get("host", DEFAULT_HTTP_HOST))
        port = int(info.get("port", 0) or 0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if probe(host, port):
                return self.url
            time.sleep(0.15)
        raise RuntimeError(f"daemon 不可连接：{self.url}")

    def stop(self) -> None:
        if self.external:
            return
        if self.server is not None:
            self.server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)


class GeometryKeeper:
    """窗口几何采集：移动/缩放防抖落盘；最大化只记标记，不覆盖还原尺寸。"""

    def __init__(self, window, state: WindowState) -> None:
        self.window = window
        self.state = state
        self.maximized = state.maximized
        self.minimized = False
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def bind(self) -> None:
        events = self.window.events
        events.resized += self._on_resized
        events.moved += self._on_moved
        events.maximized += self._on_maximized
        events.restored += self._on_restored
        minimized = getattr(events, "minimized", None)
        if minimized is not None:
            minimized += self._on_minimized

    def _on_resized(self, width: int, height: int) -> None:
        if self.maximized or self.minimized:
            return
        if int(width) < MIN_WIDTH or int(height) < MIN_HEIGHT:
            return  # 最小化/过渡态会给出 0 或极小尺寸
        self.state.width = int(width)
        self.state.height = int(height)
        self._schedule()

    def _on_moved(self, x: int, y: int) -> None:
        if self.maximized or self.minimized:
            return
        if not visible_on_screen(int(x), int(y), self.state.width, self.state.height):
            return  # 最小化坐标为 -25600/-32000，不能写回
        self.state.x = int(x)
        self.state.y = int(y)
        self._schedule()

    def _on_maximized(self, *args) -> None:
        self.maximized = True
        self.state.maximized = True
        self._schedule()

    def _on_restored(self, *args) -> None:
        self.maximized = False
        self.minimized = False
        self.state.maximized = False
        self._schedule()

    def _on_minimized(self, *args) -> None:
        self.minimized = True

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(GEOMETRY_DEBOUNCE_S, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        try:
            save_window_state(self.state)
        except OSError:
            pass


def _start_tray(show_cb, quit_cb, url: str):
    """托盘常驻；pystray 不可用时返回 None（关窗即退出）。"""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        return None

    def _on_show(icon=None, item=None) -> None:
        show_cb()

    def _on_quit(icon=None, item=None) -> None:
        quit_cb(icon)

    def _open_browser(icon=None, item=None) -> None:
        webbrowser.open(url)

    try:
        image = Image.open(app_icon())
        tray = pystray.Icon(
            "zedhub",
            image,
            APP_NAME,
            pystray.Menu(
                pystray.MenuItem("显示窗口", _on_show, default=True),
                pystray.MenuItem("在浏览器打开", _open_browser),
                pystray.MenuItem("退出", _on_quit),
            ),
        )
        tray.run_detached()
        return tray
    except Exception as exc:  # noqa: BLE001 — 托盘失败降级为普通窗口
        logx.banner(warn=f"托盘不可用，关窗即退出: {exc}")
        return None


def main() -> int:
    if sys.platform != "win32":
        _message_box("zedhub-gui 目前仅支持 Windows（依赖 WebView2）。")
        return 2

    data_dir()
    log_path = logx.init_logging(runtime_dir() / "gui.log")
    logx.install_excepthook()
    logx.banner(
        version=package_version(),
        build=build_id(),
        channel=channel(),
        gui="start",
        icon=app_icon().name,
        data=data_dir(),
        log=log_path,
    )

    lock = InstanceLock(data_dir() / "gui.lock")
    if not lock.acquire():
        _message_box("ZedHub 已在运行（请查看任务栏托盘）。")
        return 0

    host = DaemonHost()
    host.start()
    try:
        url = host.wait()
    except Exception as exc:  # noqa: BLE001
        _message_box(f"daemon 启动失败：{exc}\n日志：{log_path}")
        lock.release()
        return 1

    state = load_window_state()
    quitting = threading.Event()
    show_requested = threading.Event()
    holder: dict[str, object] = {}

    def _build_window():
        window = webview.create_window(
            GUI_TITLE,
            url,
            width=state.width,
            height=state.height,
            x=state.x,
            y=state.y,
            maximized=state.maximized,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            background_color=BACKGROUND_COLOR,
            text_select=True,
        )
        keeper = GeometryKeeper(window, state)
        keeper.bind()
        holder["window"] = window
        holder["keeper"] = keeper
        window.events.closed += lambda *args: _on_closed()
        return window

    def _on_closed() -> None:
        keeper = holder.get("keeper")
        if isinstance(keeper, GeometryKeeper):
            keeper.flush()
        holder["window"] = None
        holder["keeper"] = None
        gc.collect()  # 对齐 go-wails 关窗后的 GC / FreeOSMemory

    def _show_window() -> None:
        window = holder.get("window")
        if window is not None:
            try:
                window.show()
                window.restore()
                return
            except Exception:  # noqa: BLE001 — 窗口已销毁则走重建
                pass
        show_requested.set()

    def _quit(icon=None) -> None:
        quitting.set()
        show_requested.set()
        try:
            if icon is not None:
                icon.stop()
        except Exception:  # noqa: BLE001
            pass
        window = holder.get("window")
        if window is not None:
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass

    def _boot_tray() -> None:
        if holder.get("tray") is None:
            tray = _start_tray(_show_window, _quit, url)
            if tray is not None:
                holder["tray"] = tray

    try:
        while not quitting.is_set():
            _build_window()
            webview.start(func=_boot_tray, icon=str(app_icon()))
            if quitting.is_set():
                break
            if holder.get("tray") is None:
                break  # 托盘不可用：关窗即退出，避免主线程永久阻塞
            # 窗口已销毁（WebView2 子进程已释放）：等托盘「显示窗口」重建
            show_requested.wait()
            show_requested.clear()
    finally:
        keeper = holder.get("keeper")
        if isinstance(keeper, GeometryKeeper):
            keeper.flush()
        tray = holder.get("tray")
        if tray is not None:
            try:
                tray.stop()
            except Exception:  # noqa: BLE001
                pass
        host.stop()
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
