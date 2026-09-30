"""daemon 客户端：地址发现、buildID 握手、自动拉起（壳共享，设计 §6）。

壳（CLI/rpc/MCP 桥）只经本模块与 daemon 通信；不 import core（NFR-3）。

发现优先级（v2 §2.2）：
1. ``--host`` 参数（host:port）；
2. ``ZEDHUB_HOST`` 环境变量；
3. 地址文件（daemon 启动时写入的 runtime/daemon.json）；
4. 内置默认 127.0.0.1:8766。

自动拉起仅限生产构建且未显式指定地址；开发构建一律报错并提示先
``zedhub serve``。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .buildid import build_id, is_production_build
from .contract import (
    API_PREFIX,
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    SPAWN_WAIT_TIMEOUT_S,
    ZEDHUB_HOST_ENV,
)
from .core.errors import DaemonUnreachableError, ZedhubError, exc_from_payload


def _runtime_dir() -> Path:
    """数据目录的 runtime 子目录（与 core/paths 同一约定；壳不 import core）。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata) / "language_projects" / "zedhub"
    else:
        base = Path.home() / ".language_projects" / "zedhub"
    d = base / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_address_file() -> dict | None:
    try:
        return json.loads((_runtime_dir() / "daemon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_host(value: str) -> tuple[str, int]:
    """host[:port] → (host, port)；缺 port 用默认。"""
    v = value.strip()
    if v.startswith("["):  # [::1]:8766
        host, _, rest = v.partition("]")
        host = host[1:]
        port = int(rest.lstrip(":")) if rest.startswith(":") else DEFAULT_HTTP_PORT
        return host, port
    if ":" in v:
        host, _, port = v.rpartition(":")
        return host, int(port)
    return v, DEFAULT_HTTP_PORT


class DaemonClient:
    """带发现/拉起/握手的 HTTP 客户端。"""

    def __init__(self, explicit_host: str | None = None) -> None:
        self.explicit = explicit_host.strip() if explicit_host else None
        self._addr: tuple[str, int] | None = None
        self._addr_source: str = ""

    # -- 发现与连接 -------------------------------------------------------------

    def resolve(self) -> tuple[str, int, str]:
        """按优先级解析地址；返回 (host, port, source)。"""
        if self.explicit:
            h, p = _parse_host(self.explicit)
            return h, p, "explicit"
        env = os.environ.get(ZEDHUB_HOST_ENV)
        if env:
            h, p = _parse_host(env)
            return h, p, "env"
        info = read_address_file()
        if info and isinstance(info.get("port"), int):
            return str(info.get("host", DEFAULT_HTTP_HOST)), int(info["port"]), "address_file"
        return DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT, "default"

    def ensure_connected(self) -> tuple[str, int]:
        """确保可连：必要时自动拉起（仅生产构建且未显式指定地址）。"""
        host, port, source = self.resolve()
        if self._probe(host, port):
            self._addr, self._addr_source = (host, port), source
            return self._addr

        if source == "explicit" or source == "env":
            # 显式指向的实例不存在：绝不本地拉一个（v2 §3.2）
            raise DaemonUnreachableError(
                f"daemon 未运行: {host}:{port}（显式指定的地址，请先启动 zedhub serve）"
            )
        if not is_production_build():
            raise DaemonUnreachableError(
                "daemon 未运行（开发构建不自动拉起）；请先运行: uv run zedhub serve"
            )
        self._spawn(host, port)
        if not self._probe(host, port):
            raise DaemonUnreachableError(
                f"自动拉起后仍无法连接 {host}:{port}；请手动运行 zedhub serve 查看错误"
            )
        self._addr, self._addr_source = (host, port), "spawned"
        return self._addr

    def _probe(self, host: str, port: int) -> bool:
        """health 探测（含握手校验）。"""
        try:
            status, body = self._http("GET", f"http://{host}:{port}{API_PREFIX}/health", timeout=2.0)
            if status == 200 and body.get("ok"):
                return True
            if status == 409:
                raise exc_from_payload(
                    body.get("error", {}).get("code", "handshake_mismatch"),
                    body.get("error", {}).get("message", "buildID mismatch"),
                )
        except ZedhubError:
            raise
        except Exception:
            pass
        return False

    def _spawn(self, host: str, port: int) -> None:
        """拉起 daemon：脱离父子关系（detach），stderr 落 runtime/daemon.log。"""
        if host not in (DEFAULT_HTTP_HOST, "localhost", "::1"):
            raise DaemonUnreachableError(f"拒绝在非回环地址拉起 daemon: {host}")
        # 拉起互斥：二次探测，已有可用实例或端口被占用时放弃拉起
        import socket

        try:
            with socket.create_connection((host, port), timeout=0.5):
                return  # 竞态赢家已在监听
        except OSError:
            pass

        log = _runtime_dir() / "daemon.log"
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        with open(log, "ab") as err:
            subprocess.Popen(  # noqa: S603 — 固定 argv，无 shell
                [sys.executable, "-m", "zedhub", "serve",
                 "--auto-spawned", "--host", host, "--port", str(port)],
                stdout=subprocess.DEVNULL,
                stderr=err,
                creationflags=creationflags,
                close_fds=True,
            )
        deadline = time.monotonic() + SPAWN_WAIT_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._probe_quiet(host, port):
                return
            time.sleep(0.3)

    def _probe_quiet(self, host: str, port: int) -> bool:
        try:
            status, body = self._http("GET", f"http://{host}:{port}{API_PREFIX}/health", timeout=1.0)
            return status == 200 and body.get("ok")
        except Exception:
            return False

    # -- HTTP -------------------------------------------------------------------

    def _http(
        self,
        method: str,
        url: str,
        *,
        body: dict | None = None,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ):
        req = urllib.request.Request(url, method=method)
        if body is not None:
            req.data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Zedhub-Build", build_id())  # 握手凭据
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {
                    "ok": False,
                    "error": {"code": "internal_error", "message": raw[:300]},
                }
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise DaemonUnreachableError(f"daemon 连接失败 ({url}): {exc}") from exc

    # -- 业务调用 ----------------------------------------------------------------

    def call(self, method: str, path: str, *, query: dict | None = None, body: dict | None = None,
             timeout: float = 30.0):
        """执行一次业务请求；返回 data，业务错误还原为 ZedhubError。"""
        host, port = self.ensure_connected()
        url = f"http://{host}:{port}{path}"
        if query:
            qs = urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None}
            )
            url = f"{url}?{qs}"
        status, payload = self._http(method, url, body=body, timeout=timeout)
        if payload.get("ok"):
            return payload.get("data")
        err = payload.get("error", {})
        raise exc_from_payload(err.get("code", "internal_error"), err.get("message", f"HTTP {status}"))
