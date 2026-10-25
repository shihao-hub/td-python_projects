"""daemon 生命周期：地址文件、启动互斥、空闲退出（FR-11 / AC-19）。

- 地址文件是所有壳的共同入口：daemon 启动原子写
  ``<通道目录>/runtime/daemon.json``，退出清理；存活判定以「端口 TCP 可连
  + /health 握手通过」为准，pid 仅作诊断展示（Windows 下不用信号探活）。
- 通道隔离：地址文件、日志、数据目录按 dev/prod 分离；prod 默认固定
  8766/8765，dev 默认 0（动态探测，实际端口写地址文件）。
- 互斥：启动前探测同地址是否已有存活 daemon，端口被占用则 uvicorn bind
  失败自然退出；客户端并发拉起由同一竞争收敛为单实例。
- 空闲退出：仅 `--auto-spawned` 启用的 daemon 在「无在途请求」持续超过
  默认 30 分钟后优雅退出并清理地址文件；前台 serve 不设空闲退出。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import logx
from .buildid import build_id, package_version
from .channel import channel, data_dir, runtime_dir
from .contract import (
    DEFAULT_HTTP_HOST,
    default_http_port,
    default_ws_port,
)
from .core.errors import ZedhubError

IDLE_TIMEOUT_DEFAULT_S = 30 * 60  # v2.1：自动拉起的 daemon 空闲 30 分钟退出
IDLE_CHECK_INTERVAL_S = 60


def address_file() -> Path:
    return runtime_dir() / "daemon.json"


def daemon_log_file() -> Path:
    return runtime_dir() / "daemon.log"


def read_address() -> dict | None:
    """读取地址文件；缺失/损坏返回 None。"""
    try:
        return json.loads(address_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_address(info: dict) -> None:
    """原子写地址文件（临时文件 + os.replace，避免壳读到半截 JSON）。"""
    f = address_file()
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def remove_address() -> None:
    try:
        address_file().unlink(missing_ok=True)
    except OSError:
        pass


def port_connectable(host: str, port: int, *, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(host: str, port: int, *, timeout: float = 1.5) -> dict | None:
    """存活判定：端口可连且 /health 返回合法 JSON；返回 health.data 或 None。"""
    if not port_connectable(host, port, timeout=timeout):
        return None
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://{host}:{port}/api/v1/health", timeout=timeout
        ) as resp:
            body = json.loads(resp.read().decode())
            return body.get("data") if body.get("ok") else None
    except (OSError, json.JSONDecodeError):
        return None


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


class InstanceLock:
    """跨进程启动互斥：OS 文件锁，进程退出由内核自动释放（无陈旧锁）。

    动态端口下不能靠「端口已被占用」判定互斥——两个进程会各自拿到不同
    随机端口；固定地址文件又有探测竞态。文件锁是唯一可靠的单实例真源。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            fh.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


class IdleWatchdog:
    """空闲退出计时：仅在「在途请求数 = 0」期间累计；请求结束即重置。"""

    def __init__(self, timeout_s: float = IDLE_TIMEOUT_DEFAULT_S) -> None:
        self.timeout_s = timeout_s
        self._task: asyncio.Task | None = None

    def start(self, server, state) -> None:
        async def _run() -> None:
            last_active = time.monotonic()
            while True:
                await asyncio.sleep(IDLE_CHECK_INTERVAL_S)
                if state.inflight > 0:
                    last_active = state.last_activity
                    continue
                idle = time.monotonic() - max(last_active, state.last_activity)
                if idle >= self.timeout_s:
                    server.should_exit = True  # 优雅关闭：等在途、删地址文件
                    return

        self._task = asyncio.get_running_loop().create_task(_run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


def _existing_daemon(host: str, port: int) -> dict | None:
    """启动互斥探测：显式端口直探；动态端口只认地址文件。"""
    if port > 0:
        return probe(host, port)
    info = read_address()
    if not info or not isinstance(info.get("port"), int):
        return None
    return probe(str(info.get("host", host)), int(info["port"]))


def serve_with_lifecycle(
    *,
    host: str = DEFAULT_HTTP_HOST,
    port: int | None = None,
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
    auto_spawned: bool = False,
    idle_timeout_s: float = IDLE_TIMEOUT_DEFAULT_S,
    ws_port: int | None = None,
) -> None:
    """带生命周期管理的 daemon 启动（serve 命令的实际实现）。"""
    actual_port = default_http_port() if port is None else port
    actual_ws_port = default_ws_port() if ws_port is None else ws_port

    lock = InstanceLock(runtime_dir() / "daemon.lock")
    if not lock.acquire():
        existing = _existing_daemon(host, actual_port)
        detail = (
            f"（pid={existing.get('pid')}，build={existing.get('build_id')}）"
            if existing
            else ""
        )
        raise ZedhubError(f"daemon 已在运行{detail}，地址文件见 {address_file()}")

    from .http_api import run_server

    watchdog: IdleWatchdog | None = IdleWatchdog(idle_timeout_s) if auto_spawned else None

    def on_start(server, app, http_port: int, ws_port_actual: int) -> None:
        write_address(
            {
                "host": host,
                "port": http_port,
                "ws_port": ws_port_actual,
                "pid": os.getpid(),
                "build_id": build_id(),
                "version": package_version(),
                "channel": channel(),
                "data_dir": str(data_dir()),
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "auto_spawned": auto_spawned,
            }
        )
        logx.banner(
            version=package_version(),
            build=build_id(),
            channel=channel(),
            pid=os.getpid(),
            host=host,
            port=http_port,
            ws=ws_port_actual or "off",
            data=data_dir(),
            log=logx.log_path(),
        )
        print(
            f"zedhub daemon: http://{_url_host(host)}:{http_port}/ui  "
            f"(data: {data_dir()})",
            file=sys.stderr,
            flush=True,
        )
        if watchdog is not None:
            app.state.daemon.last_activity = time.monotonic()
            watchdog.start(server, app.state.daemon)

    async def on_shutdown() -> None:
        if watchdog is not None:
            await watchdog.stop()
        remove_address()

    try:
        run_server(
            host=host,
            port=actual_port,
            zed_db=zed_db,
            opencode_db=opencode_db,
            on_start=on_start,
            on_shutdown=on_shutdown,
            ws_port=actual_ws_port,
        )
    except Exception:
        traceback.print_exc()
        remove_address()
        raise
    finally:
        lock.release()
