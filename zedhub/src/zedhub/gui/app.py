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
import os
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
BACKGROUND_COLOR = "#121316"  # 与页面暗色底一致，避免首帧闪白
WEBVIEW_DATA_DIRNAME = "webview"  # WebView2 站点数据（含主题偏好）落数据目录
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19


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


class ConsoleQuitHook:
    """控制台 Ctrl+C / Ctrl+Break / 关控制台时优雅退出。

    pywebview 在 Windows 把主线程交给 WinForms 消息循环，Python 的 SIGINT
    只有在回到解释器字节码时才会派发，默认 Ctrl+C 因此毫无反应。Windows 的
    ``SetConsoleCtrlHandler`` 回调跑在独立线程，可安全触发窗口销毁与收尾。
    """

    def __init__(self, on_quit) -> None:
        self._on_quit = on_quit
        self._callback = (
            ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)(self._handle)
            if sys.platform == "win32"
            else None
        )
        self._installed = False

    def _handle(self, event: int) -> bool:
        try:
            self._on_quit()
        except Exception:  # noqa: BLE001 — 控制台回调里不能抛
            return False
        return True

    def install(self) -> bool:
        if self._callback is None:
            return False
        try:
            self._installed = bool(
                ctypes.windll.kernel32.SetConsoleCtrlHandler(self._callback, True)
            )
        except (AttributeError, OSError):
            self._installed = False
        return self._installed

    def uninstall(self) -> None:
        if not self._installed:
            return
        try:
            ctypes.windll.kernel32.SetConsoleCtrlHandler(self._callback, False)
        except (AttributeError, OSError):
            pass
        self._installed = False


class TitleBarTheme:
    """Windows 标题栏明暗跟随页面主题（DWM immersive dark mode）。

    页面 JS 通过 pywebview 的 ``set_theme`` 桥调用；浏览器打开时没有这个桥，
    页面主题仍由 localStorage 自行维持。``hwnd`` 每次按窗口标题重找，
    以适配「关窗销毁 → 托盘重建」的生命周期。
    """

    def __init__(self, title: str = GUI_TITLE) -> None:
        self.title = title

    def _hwnd(self) -> int:
        try:
            user32 = ctypes.windll.user32
            user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            user32.FindWindowW.restype = ctypes.c_void_p
            return int(user32.FindWindowW(None, self.title) or 0)
        except (AttributeError, OSError):
            return 0

    def apply(self, theme: str) -> bool:
        hwnd = self._hwnd()
        if not hwnd:
            return False
        value = ctypes.c_int(1 if theme != "light" else 0)
        attrs = (DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY)
        for attr in attrs:
            try:
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd),
                    attr,
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
            except (AttributeError, OSError):
                return False
            if result == 0:
                return True
        return False

    def set_theme(self, theme: str) -> bool:
        """pywebview js_api：页面传入 ``dark`` / ``light``。"""
        return self.apply(theme)


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

    # 裁剪 WebView2 独立 GPU / Network 进程（对齐 071 知识库，压低开窗峰值 ~100MB）
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--disable-gpu --disable-gpu-compositing --disable-software-rasterizer "
        "--renderer-process-limit=1 --disable-features=NetworkServiceOutOfProcess",
    )

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
    titlebar = TitleBarTheme()
    webview_data = data_dir() / WEBVIEW_DATA_DIRNAME
    webview_data.mkdir(parents=True, exist_ok=True)
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
            js_api=titlebar,
        )
        keeper = GeometryKeeper(window, state)
        keeper.bind()
        holder["window"] = window
        holder["keeper"] = keeper
        window.events.shown += lambda *args: titlebar.apply("dark")
        window.events.closed += lambda *args: _on_closed()
        return window

    def _on_closed() -> None:
        keeper = holder.get("keeper")
        if isinstance(keeper, GeometryKeeper):
            keeper.flush()
        holder["window"] = None
        holder["keeper"] = None
        gc.collect()  # 对齐 go-wails 关窗后的 GC
        if sys.platform == "win32":
            try:
                ctypes.windll.psapi.EmptyWorkingSet(
                    ctypes.windll.kernel32.GetCurrentProcess()
                )
            except OSError:
                pass

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

    console_quit = ConsoleQuitHook(_quit)
    console_quit.install()
    try:
        while not quitting.is_set():
            _build_window()
            webview.start(
                func=_boot_tray,
                icon=str(app_icon()),
                private_mode=False,
                storage_path=str(webview_data),
            )
            if quitting.is_set():
                break
            if holder.get("tray") is None:
                break  # 托盘不可用：关窗即退出，避免主线程永久阻塞
            # 窗口已销毁（WebView2 子进程已释放）：等托盘「显示窗口」重建
            show_requested.wait()
            show_requested.clear()
    finally:
        console_quit.uninstall()
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
