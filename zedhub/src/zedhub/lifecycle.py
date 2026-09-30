"""daemon 生命周期：地址文件、启动互斥、空闲退出（FR-11 / AC-19）。

- 地址文件是所有壳的共同入口：daemon 启动原子写 `runtime/daemon.json`，
  退出清理；存活判定以「端口 TCP 可连 + /health 握手通过」为准，pid 仅作
  诊断展示（Windows 下不用信号探活）。
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
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .contract import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT
from .core.errors import ZedhubError
from .core.paths import data_subdir

IDLE_TIMEOUT_DEFAULT_S = 30 * 60  # v2.1：自动拉起的 daemon 空闲 30 分钟退出
IDLE_CHECK_INTERVAL_S = 60


def address_file() -> Path:
    return data_subdir("runtime") / "daemon.json"


def daemon_log_file() -> Path:
    return data_subdir("runtime") / "daemon.log"


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


def serve_with_lifecycle(
    *,
    host: str = DEFAULT_HTTP_HOST,
    port: int = DEFAULT_HTTP_PORT,
    zed_db: Path | None = None,
    opencode_db: Path | None = None,
    auto_spawned: bool = False,
    idle_timeout_s: float = IDLE_TIMEOUT_DEFAULT_S,
) -> None:
    """带生命周期管理的 daemon 启动（serve 命令的实际实现）。"""
    existing = probe(host, port)
    if existing is not None:
        raise ZedhubError(
            f"daemon 已在运行（pid={existing.get('pid')}，build={existing.get('build_id')}），"
            "地址文件见 runtime/daemon.json"
        )

    from .buildid import build_id, package_version
    from .http_api import run_server

    watchdog: IdleWatchdog | None = IdleWatchdog(idle_timeout_s) if auto_spawned else None

    def on_start(server, app) -> None:
        write_address(
            {
                "host": host,
                "port": port,
                "pid": os.getpid(),
                "build_id": build_id(),
                "version": package_version(),
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "auto_spawned": auto_spawned,
            }
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
            port=port,
            zed_db=zed_db,
            opencode_db=opencode_db,
            on_start=on_start,
            on_shutdown=on_shutdown,
        )
    except Exception:
        traceback.print_exc()
        remove_address()
        raise
