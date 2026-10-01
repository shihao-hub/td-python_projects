# /// script
# requires-python = ">=3.12"
# dependencies = ["websocket-client>=1.8"]
# ///
"""抖音视频下载器（CDP 版，uv 单脚本）。

用法：
    uv run douyin_dl.py "<含抖音链接的文本>" [--json] [--output-dir DIR]
    Get-Content 文案.txt -Raw | uv run douyin_dl.py --json
    uv run douyin_dl.py schema          # 导出 CLI 契约 JSON（零业务 I/O）

分层（单文件内，遵循《CLI 工具开发标准》的 Service 核心 + 薄壳原则）：
    - 契约层：错误码、JSON 包络、schema 定义（不导入任何业务依赖）
    - 基础设施层：Chrome 启动、CDP 调用、HTTP 下载（唯一接触外部资源的地方）
    - Service 层：文本提链、链接归一化、批量下载编排（不读写标准流、不退出进程）
    - CLI 适配层：参数解析、人读/JSON 渲染、退出码映射
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import websocket

VERSION = "2.0.0"
PROG = "douyin_dl"
# 项目目录名（monorepo 目录名）用下划线，与 CLI 程序名 PROG 区分
PROJECT_DIR_NAME = "douyin_downloader"

# sh-ai-todo: 将整个文件改造成 uv 的单脚本启动文件

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEBUG_PORT = 9222
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# --------------------------------------------------------------------------
# 契约层：错误码、JSON 包络、schema 定义（零业务 I/O）
# --------------------------------------------------------------------------

ERROR_MESSAGES = {
    "no_input": "输入为空，请传入文本参数或通过管道提供内容",
    "no_url": "输入文本中未发现任何链接",
    "no_douyin_url": "输入文本中没有抖音链接，全部已跳过",
    "chrome_launch_failed": "Chrome 启动失败",
    "invalid_url": "抖音链接非法或短链解析失败，未能得到视频 ID",
    "stream_not_found": "未能在页面中捕获视频流（可能需要人工完成验证）",
    "download_failed": "视频流下载失败",
}


@dataclass
class AppError(Exception):
    """公共业务错误：稳定 code + 可公开 message，不含渠道/退出码语义。"""

    code: str
    message: str
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - 仅用于诊断输出
        return f"{self.code}: {self.message}{(' - ' + self.detail) if self.detail else ''}"


def _obj(properties: dict[str, Any], required: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _schema_text_property() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": 100000,
        "description": "含抖音链接的文本，可包含多个链接与无关文案",
    }


def _schema_response_property() -> dict[str, Any]:
    item = _obj(
        {
            "input_url": {"type": "string", "description": "文本中提取到的原始链接"},
            "video_url": {"type": "string", "description": "归一化后的视频页地址；失败时为空串"},
            "video_id": {"type": "string", "description": "抖音视频 ID；失败时为空串"},
            "ok": {"type": "boolean"},
            "title": {"type": "string", "description": "页面标题清洗后的文件名主干"},
            "path": {"type": "string", "description": "落盘绝对路径；失败时为空串"},
            "bytes": {"type": "integer", "minimum": 0},
            "error": _obj(
                {
                    "code": {"type": "string", "enum": sorted(ERROR_MESSAGES)},
                    "message": {"type": "string"},
                    "detail": {"type": "string"},
                },
                required=["code", "message"],
            ),
        },
        required=["input_url", "video_url", "video_id", "ok", "title", "path", "bytes"],
    )
    skipped = _obj(
        {
            "url": {"type": "string"},
            "reason": {"type": "string", "enum": ["not_douyin"]},
        },
        required=["url", "reason"],
    )
    data = _obj(
        {
            "downloaded": {"type": "array", "items": item},
            "skipped": {"type": "array", "items": skipped},
            "summary": _obj(
                {
                    "total": {"type": "integer", "minimum": 0},
                    "succeeded": {"type": "integer", "minimum": 0},
                    "failed": {"type": "integer", "minimum": 0},
                    "skipped": {"type": "integer", "minimum": 0},
                },
                required=["total", "succeeded", "failed", "skipped"],
            ),
        },
        required=["downloaded", "skipped", "summary"],
    )
    return _obj(
        {
            "ok": {"type": "boolean"},
            "data": data,
            "error": _obj(
                {
                    "code": {"type": "string", "enum": sorted(ERROR_MESSAGES)},
                    "message": {"type": "string"},
                    "detail": {"type": "string"},
                },
                required=["code", "message"],
            ),
        },
        required=["ok", "data"],
    )


def build_schema(defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """构建 CLI 契约目录。纯内存构造，不触发 Chrome、网络或文件 I/O。"""
    resolved = {
        "output_dir": os.path.join(os.path.expanduser("~"), "Downloads"),
        "debug_port": DEBUG_PORT,
        "chrome": CHROME_PATH,
    }
    resolved.update(defaults or {})
    response = _schema_response_property()
    return {
        "name": PROG,
        "version": VERSION,
        "interface": "cli",
        "description": "从一段文本中提取抖音视频链接，经 Chrome CDP 捕获无水印视频流并下载到本地",
        "commands": [
            {
                "name": PROG,
                "summary": "提取文本中的抖音链接并下载（串行逐个处理，非抖音链接跳过）",
                "input": {
                    "text": {
                        "type": "array",
                        "items": _schema_text_property(),
                        "description": "位置参数，多段以换行拼接；缺省且 stdin 非 TTY 时读取 stdin，'-' 显式表示 stdin",
                    },
                    "options": {
                        "json": {"type": "boolean", "default": False, "description": "输出 JSON 包络"},
                        "schema": {"type": "boolean", "default": False, "description": "仅输出本契约 JSON 并退出"},
                        "output_dir": {"type": "string", "default": resolved["output_dir"], "description": "下载目录"},
                        "debug_port": {"type": "integer", "default": resolved["debug_port"], "description": "Chrome CDP 端口"},
                        "profile_dir": {"type": "string", "default": "", "description": "Chrome 专用 Profile 目录，空表示使用默认数据目录"},
                        "chrome": {"type": "string", "default": resolved["chrome"], "description": "Chrome 可执行文件路径"},
                    },
                },
                "output": {
                    "envelope": response,
                    "success": {"ok": True, "data": "<OutputData>"},
                    "partial_failure": {"ok": False, "data": "<OutputData>", "error": "<Error>"},
                    "failure": {"ok": False, "error": "<Error>", "data": "<OutputData，可能为空集合>"},
                },
                "exit_codes": {
                    "0": "全部链接下载成功",
                    "1": "存在失败项或部分成功",
                    "2": "调用参数错误，或未发现可处理的抖音链接",
                },
                "constraints": [
                    "处理串行执行，复用同一个 Chrome 与标签页",
                    "单条失败不中断整批，结果中逐条给出结构化错误",
                    "需要人工完成抖音验证滑块时该条失败，人工处理后可重跑",
                ],
            }
        ],
        "side_effects": {
            "filesystem": [resolved["output_dir"]],
            "process": ["Chrome（独立 Profile + CDP 调试端口）"],
            "network": ["抖音页面与 douyinvod.com 视频流"],
        },
        "not_provided": {
            "mcp": "本工具为本地一次性下载动作，无跨会话状态查询需求，按标准 §5.5 以 interface=cli 声明契约",
        },
    }


def envelope_ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": data}


def envelope_error(code: str, message: str, data: dict[str, Any] | None = None, detail: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {"code": code, "message": message, "detail": detail},
    }
    if data is not None:
        payload["data"] = data
    return payload


# --------------------------------------------------------------------------
# 基础设施层：数据目录、Chrome 启动、CDP、HTTP 下载
# --------------------------------------------------------------------------


def data_dir() -> str:
    """项目自有数据的唯一根目录：%APPDATA%\\language_projects\\douyin_downloader\\。"""
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, "language_projects", PROJECT_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def default_profile_dir() -> str:
    return os.path.join(data_dir(), "chrome-profile")


def default_download_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Downloads")


def _run_hidden(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.run(args, creationflags=flags, **kwargs)


def cdpx_url(port: int, path: str) -> str:
    return f"http://127.0.0.1:{port}{path}"


def chrome_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(cdpx_url(port, "/json/version"), timeout=1):
            return True
    except Exception:
        return False


def _seed_profile_preferences(profile_dir: str, download_dir: str) -> None:
    """写入 Chrome 偏好：默认下载目录指向本项目下载目录（等价原 PS1 逻辑）。"""
    pref_dir = os.path.join(profile_dir, "Default")
    os.makedirs(pref_dir, exist_ok=True)
    pref_file = os.path.join(pref_dir, "Preferences")

    prefs: dict[str, Any] = {}
    if os.path.exists(pref_file):
        try:
            with open(pref_file, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                prefs = loaded
        except (json.JSONDecodeError, OSError):
            prefs = {}

    download = prefs.get("download")
    if not isinstance(download, dict):
        download = {}
    download["default_directory"] = download_dir
    download["directory_upgrade"] = True
    download["prompt_for_download"] = False
    prefs["download"] = download

    try:
        with open(pref_file, "w", encoding="utf-8") as fh:
            json.dump(prefs, fh, ensure_ascii=False, indent=2)
    except OSError:
        # 偏好文件写不进去不影响主流程（实际下载由 Python 侧完成）
        pass


def start_chrome(
    url: str = "https://www.douyin.com",
    *,
    chrome_path: str = CHROME_PATH,
    profile_dir: str | None = None,
    port: int = DEBUG_PORT,
    download_dir: str | None = None,
) -> int:
    """启动独立的 Chrome 自动化窗口（持久化 Profile + CDP 调试端口）。

    sh-ai-todo: 改为子进程执行 start_chrome 函数，这个函数的作用就是启动这个 start_chrome.ps1 脚本，但是我希望这个脚本内容是嵌在 start_chrome 函数里的

    实现说明：内嵌原 start_chrome.ps1 的 PowerShell 内容，经 -EncodedCommand（UTF-16LE + base64）
    交给子进程执行。之所以仍走 PowerShell：脚本用 Invoke-CimMethod Win32_Process Create 启动
    Chrome，属于「创建即返回」的非阻塞启动，可避免 & 调用符在进程存活期间挂住 Python。
    """
    if not os.path.exists(chrome_path):
        raise AppError("chrome_launch_failed", f"未找到 Chrome 可执行文件: {chrome_path}")

    profile = profile_dir or default_profile_dir()
    downloads = download_dir or default_download_dir()
    os.makedirs(profile, exist_ok=True)
    _seed_profile_preferences(profile, downloads)

    ps1 = _build_embedded_ps1(chrome_path, profile, port, url)
    encoded = base64.b64encode(ps1.encode("utf-16-le")).decode("ascii")
    proc = _run_hidden(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
    )
    output = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
    if proc.returncode != 0:
        raise AppError("chrome_launch_failed", "PowerShell 启动 Chrome 失败", output[:500])

    match = re.search(r"ProcessId:\s*(\d+)", output)
    if not match:
        raise AppError("chrome_launch_failed", "未能解析 Chrome 进程号", output[:500])
    return int(match.group(1))


def _build_embedded_ps1(chrome_path: str, profile_dir: str, port: int, url: str) -> str:
    """生成内嵌 PowerShell 脚本（单引号字符串，内部单引号需翻倍转义）。"""

    def q(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    return (
        "$chrome = " + q(chrome_path) + "\n"
        "$profile = " + q(profile_dir) + "\n"
        "$Url = " + q(url) + "\n"
        f"$port = {port}\n"
        "\n"
        "# Ensure preferences (Downloads folder and developer mode)\n"
        '$prefDir = Join-Path $profile "Default"\n'
        "if (!(Test-Path $prefDir)) {\n"
        "    New-Item -ItemType Directory -Path $prefDir -Force | Out-Null\n"
        "}\n"
        "\n"
        '$cmdline = "`"$chrome`" --user-data-dir=`"$profile`" --no-first-run '
        "--no-default-browser-check --remote-debugging-port=$port --remote-allow-origins=* "
        '`"$Url`""\n'
        "\n"
        "$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
        "@{ CommandLine = $cmdline }\n"
        'Write-Output ("ReturnCode: " + $res.ReturnValue + ", ProcessId: " + $res.ProcessId)\n'
    )


def ensure_chrome_running(
    port: int = DEBUG_PORT,
    *,
    chrome_path: str = CHROME_PATH,
    profile_dir: str | None = None,
    download_dir: str | None = None,
) -> bool:
    if chrome_ready(port):
        return True

    print("[*] Launching Chrome automation window (with persistent profile)...", file=sys.stderr)
    start_chrome(
        "https://www.douyin.com",
        chrome_path=chrome_path,
        profile_dir=profile_dir,
        port=port,
        download_dir=download_dir,
    )

    for _ in range(15):
        time.sleep(1)
        if chrome_ready(port):
            print(f"[+] Chrome CDP ready on port {port}.", file=sys.stderr)
            return True
    raise AppError("chrome_launch_failed", f"等待 Chrome CDP 端口 {port} 超时")


def get_targets(port: int = DEBUG_PORT) -> list[dict[str, Any]]:
    with urllib.request.urlopen(cdpx_url(port, "/json"), timeout=5) as resp:
        return json.load(resp)


def eval_cdp(ws_url: str, expression: str, timeout: float = 20.0) -> Any:
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        msg_id = int(time.time() * 1000) % 1000000
        ws.send(
            json.dumps(
                {
                    "id": msg_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                }
            )
        )
        while True:
            res = json.loads(ws.recv())
            if res.get("id") == msg_id:
                return res.get("result", {}).get("result", {}).get("value")
    finally:
        ws.close()


def navigate_page(ws_url: str, url: str, timeout: float = 20.0) -> None:
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        ws.send(json.dumps({"id": 101, "method": "Page.navigate", "params": {"url": url}}))
        while True:
            res = json.loads(ws.recv())
            if res.get("id") == 101:
                break
    finally:
        ws.close()


def open_douyin_tab(target_url: str, port: int = DEBUG_PORT) -> str:
    """复用已有抖音标签页，必要时新建，返回该页的 webSocketDebuggerUrl。"""
    tabs = [t for t in get_targets(port) if t.get("type") == "page" and "douyin.com" in t.get("url", "")]
    if tabs:
        page = tabs[0]
        ws_url = page["webSocketDebuggerUrl"]
        if target_url not in page.get("url", ""):
            print(f"[*] Navigating page to: {target_url}", file=sys.stderr)
            navigate_page(ws_url, target_url)
            time.sleep(3)
        return ws_url

    put_url = cdpx_url(port, f"/json/new?{urllib.parse.quote(target_url, safe='')}")
    req = urllib.request.Request(put_url, method="PUT")
    page = json.load(urllib.request.urlopen(req, timeout=10))
    time.sleep(3)
    return page["webSocketDebuggerUrl"]


def unique_path(directory: str, stem: str, suffix: str = ".mp4") -> str:
    """生成不覆盖既有文件的输出路径（冲突时追加 _1、_2 …）。

    文件名主干统一在此清洗非法字符，调用方无需重复处理。
    """
    os.makedirs(directory, exist_ok=True)
    safe_stem = clean_title(stem, f"douyin_{int(time.time())}")
    candidate = os.path.join(directory, f"{safe_stem}{suffix}")
    index = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{safe_stem}_{index}{suffix}")
        index += 1
    return candidate


def download_stream(video_url: str, output_path: str) -> tuple[str, int]:
    """下载视频流到指定路径（调用方保证 output_path 不冲突）。返回 (路径, 字节数)。"""
    req = urllib.request.Request(
        video_url,
        headers={"User-Agent": USER_AGENT, "Referer": "https://www.douyin.com/"},
    )
    downloaded = 0
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(output_path, "wb") as fh:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                downloaded += len(chunk)
                print(f"\rProgress: {downloaded / (1024 * 1024):.2f} MB", end="", file=sys.stderr, flush=True)
    except Exception:
        if downloaded == 0 and os.path.exists(output_path):
            os.remove(output_path)
        raise
    print(file=sys.stderr)
    return output_path, downloaded


# --------------------------------------------------------------------------
# Service 层：文本提链、链接归一化、批量下载编排
# --------------------------------------------------------------------------

_URL_RE = re.compile(r"""https?://[^\s<>"'`（）()【】\[\]{}，。；、]+""", re.IGNORECASE)
_TRAILING = "\"'`.,;:!?，。；：！？、）)】」》>"
_DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")


@dataclass
class SkippedLink:
    url: str
    reason: str


@dataclass
class ExtractResult:
    douyin: list[str] = field(default_factory=list)
    skipped: list[SkippedLink] = field(default_factory=list)


@dataclass
class DownloadRecord:
    input_url: str
    ok: bool
    video_url: str = ""
    video_id: str = ""
    title: str = ""
    path: str = ""
    bytes: int = 0
    error_code: str = ""
    error_message: str = ""
    error_detail: str = ""

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "input_url": self.input_url,
            "video_url": self.video_url,
            "video_id": self.video_id,
            "ok": self.ok,
            "title": self.title,
            "path": self.path,
            "bytes": self.bytes,
        }
        if not self.ok:
            payload["error"] = {
                "code": self.error_code,
                "message": self.error_message,
                "detail": self.error_detail,
            }
        return payload


@dataclass
class RunResult:
    records: list[DownloadRecord] = field(default_factory=list)
    skipped: list[SkippedLink] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.records if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.records if not r.ok)

    def to_data(self) -> dict[str, Any]:
        return {
            "downloaded": [r.to_json() for r in self.records],
            "skipped": [{"url": s.url, "reason": s.reason} for s in self.skipped],
            "summary": {
                "total": len(self.records),
                "succeeded": self.succeeded,
                "failed": self.failed,
                "skipped": len(self.skipped),
            },
        }


def is_douyin_url(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    host = host.lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return any(host == base or host.endswith("." + base) for base in _DOUYIN_HOSTS)


def extract_links(text: str) -> ExtractResult:
    """从任意文本中提取链接：抖音链接按出现顺序去重收集，其余记为 skipped。"""
    result = ExtractResult()
    seen: set[str] = set()
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(_TRAILING)
        if not url or url in seen:
            continue
        seen.add(url)
        if is_douyin_url(url):
            result.douyin.append(url)
        else:
            result.skipped.append(SkippedLink(url=url, reason="not_douyin"))
    return result


def resolve_video_url(url: str) -> str:
    """跟随重定向解析短链，尽量归一为 https://www.douyin.com/video/<id>。"""
    final_url = url
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
    try:
        with opener.open(req, timeout=15) as resp:
            resolved = resp.geturl()
            if resolved:
                final_url = resolved
    except (urllib.error.URLError, OSError, ValueError):
        final_url = url

    match = re.search(r"/video/(\d+)", final_url)
    if match:
        return f"https://www.douyin.com/video/{match.group(1)}"
    match = re.search(r"/video/(\d+)", url)
    if match:
        return f"https://www.douyin.com/video/{match.group(1)}"
    return final_url


def extract_video_id(video_url: str) -> str:
    match = re.search(r"/video/(\d+)", video_url)
    return match.group(1) if match else ""


def clean_title(title: str, fallback: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|\r\n\t]', "_", title or "")[:50].strip(" ._")
    return cleaned or fallback


def capture_stream_url(ws_url: str, attempts: int = 12, interval: float = 1.0) -> str | None:
    """在页面中轮询捕获视频流地址（优先 video 元素，其次 CDN 资源记录）。"""
    for _ in range(attempts):
        srcs = (
            eval_cdp(
                ws_url,
                "Array.from(document.querySelectorAll('video'))"
                ".map(v => v.currentSrc || v.src).filter(Boolean)",
            )
            or []
        )
        for src in srcs:
            if src.startswith("http") and not src.startswith("blob:"):
                return src

        urls = (
            eval_cdp(
                ws_url,
                "performance.getEntriesByType('resource').map(r => r.name)"
                ".filter(n => n.includes('.douyinvod.com') && !n.includes('media-audio'))",
            )
            or []
        )
        if urls:
            return urls[-1]

        time.sleep(interval)
    return None


def download_one(
    input_url: str,
    *,
    output_dir: str,
    port: int,
    tab_ws_url: str,
    emit: Callable[[str], None],
) -> DownloadRecord:
    """处理单个抖音链接。捕获全部异常并转为结构化错误，保证单条失败不中断整批。"""
    record = DownloadRecord(input_url=input_url, ok=False)
    try:
        video_url = resolve_video_url(input_url)
        record.video_url = video_url
        record.video_id = extract_video_id(video_url)
        if not record.video_id:
            raise AppError("invalid_url", ERROR_MESSAGES["invalid_url"], f"解析结果: {video_url}")

        if video_url not in input_url:
            emit(f"[*] Resolved: {input_url} -> {video_url}")

        # 若当前标签页不在目标页面，先导航过去
        try:
            current = get_targets(port)
        except Exception:
            current = []
        for tab in current:
            if tab.get("webSocketDebuggerUrl") == tab_ws_url and video_url in tab.get("url", ""):
                break
        else:
            emit(f"[*] Navigating page to: {video_url}")
            navigate_page(tab_ws_url, video_url)
            time.sleep(3)

        title = eval_cdp(tab_ws_url, "document.title") or ""
        stream_url = capture_stream_url(tab_ws_url)
        if not stream_url:
            raise AppError("stream_not_found", ERROR_MESSAGES["stream_not_found"])

        stem = clean_title(str(title), f"douyin_{record.video_id}")
        output_path = unique_path(output_dir, stem)
        emit(f"[+] Found stream URL: {stream_url[:90]}...")
        saved_path, size = download_stream(stream_url, output_path)

        record.ok = True
        record.title = stem
        record.path = saved_path
        record.bytes = size
        emit(f"[+] Download complete: {saved_path} ({size} bytes)")
    except AppError as exc:
        record.error_code = exc.code
        record.error_message = exc.message
        record.error_detail = exc.detail
    except Exception as exc:  # 基础设施异常统一归类，不向用户抛裸栈
        record.error_code = "download_failed"
        record.error_message = ERROR_MESSAGES["download_failed"]
        record.error_detail = f"{type(exc).__name__}: {exc}"
    return record


def run_downloads(
    text: str,
    *,
    output_dir: str,
    port: int,
    chrome_path: str = CHROME_PATH,
    profile_dir: str | None = None,
    emit: Callable[[str], None] = lambda _msg: None,
) -> tuple[RunResult | None, AppError | None]:
    """编排：提取链接 → 启动/复用 Chrome → 串行逐条下载 → 汇总。

    返回 (结果, 错误)：入口级错误（无链接、Chrome 启动失败）时结果可能为 None 或已含 skipped。
    """
    extracted = extract_links(text)
    result = RunResult(records=[], skipped=extracted.skipped)

    if not extracted.douyin:
        code = "no_douyin_url" if extracted.skipped else "no_url"
        return (result if extracted.skipped else None), AppError(code, ERROR_MESSAGES[code])

    try:
        ensure_chrome_running(port, chrome_path=chrome_path, profile_dir=profile_dir, download_dir=output_dir)
    except AppError as exc:
        return result, exc

    total = len(extracted.douyin)
    for index, url in enumerate(extracted.douyin, start=1):
        emit(f"[*] ({index}/{total}) {url}")
        try:
            tab_ws_url = open_douyin_tab(resolve_video_url(url), port)
        except AppError as exc:
            result.records.append(
                DownloadRecord(
                    input_url=url,
                    ok=False,
                    error_code=exc.code,
                    error_message=exc.message,
                    error_detail=exc.detail,
                )
            )
            continue
        except Exception as exc:
            result.records.append(
                DownloadRecord(
                    input_url=url,
                    ok=False,
                    error_code="chrome_launch_failed",
                    error_message=ERROR_MESSAGES["chrome_launch_failed"],
                    error_detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        result.records.append(
            download_one(url, output_dir=output_dir, port=port, tab_ws_url=tab_ws_url, emit=emit)
        )
    return result, None


# --------------------------------------------------------------------------
# CLI 适配层：参数解析、渲染、退出码
# --------------------------------------------------------------------------


def ensure_utf8_stdio() -> None:
    """强制标准流为 UTF-8：Windows 控制台默认 cp936 会把 JSON 转义成非 UTF-8 字节，
    导致重定向到文件或用其他工具读取时出现乱码。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def read_stdin_text() -> str:
    """阻塞读取 stdin 全文（管道/重定向场景）。"""
    try:
        data = sys.stdin.buffer.read()
    except (OSError, ValueError, AttributeError):
        try:
            return sys.stdin.read()
        except (OSError, ValueError):
            return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def collect_text(args: argparse.Namespace) -> str:
    chunks = [chunk for chunk in (args.text or []) if chunk is not None]
    text = "\n".join(chunks)
    explicit_stdin = len(chunks) == 1 and chunks[0].strip() == "-"
    implicit_stdin = not text.strip() and not sys.stdin.isatty()
    if explicit_stdin or implicit_stdin:
        text = read_stdin_text()
    # 支持把多行文案整段贴进一个参数（字面 \n）
    return text.replace("\\n", "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="从文本中提取抖音视频链接并下载（非抖音链接跳过；多链接串行逐个处理）",
        epilog=(
            "示例：\n"
            f"  uv run {PROG}.py \"8.74 复制打开抖音 https://v.douyin.com/EBgtkB68340/\"\n"
            f"  Get-Content 文案.txt -Raw | uv run {PROG}.py --json\n"
            f"  uv run {PROG}.py schema"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="含抖音链接的文本（多段以换行拼接；缺省且 stdin 非 TTY 时读 stdin，'-' 显式表示 stdin）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 包络（stdout 仅一个 JSON 对象）")
    parser.add_argument("--schema", action="store_true", help="仅输出 CLI 契约 JSON 并退出")
    parser.add_argument("--output-dir", default=default_download_dir(), help="下载目录（默认 ~/Downloads）")
    parser.add_argument("--debug-port", type=int, default=DEBUG_PORT, help=f"Chrome CDP 端口（默认 {DEBUG_PORT}）")
    parser.add_argument("--profile-dir", default=None, help="Chrome 专用 Profile 目录（默认项目数据目录下 chrome-profile）")
    parser.add_argument("--chrome", default=CHROME_PATH, help="Chrome 可执行文件路径")
    parser.add_argument("--version", action="version", version=f"{PROG} {VERSION}")
    return parser


def render_text(result: RunResult, output_dir: str) -> None:
    for record in result.records:
        if record.ok:
            print(f"[OK]   {record.video_url}")
            print(f"       文件: {record.path} ({record.bytes} bytes)")
        else:
            print(f"[FAIL] {record.input_url}")
            print(f"       原因: {record.error_code} - {record.error_message}")
            if record.error_detail:
                print(f"       详情: {record.error_detail}")
    for skipped in result.skipped:
        print(f"[SKIP] {skipped.url}（非抖音链接）")
    print(
        f"汇总: 共 {len(result.records)} 条，成功 {result.succeeded}，失败 {result.failed}，"
        f"跳过 {len(result.skipped)}；下载目录 {output_dir}"
    )
    if result.failed and not result.succeeded:
        print("提示: 若 Chrome 窗口出现验证滑块，请人工完成后重跑。", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    # 契约导出分支：零业务 I/O，必须先于任何目录/进程操作
    # 仅当首个位置参数是唯一的 "schema" 时才走子命令语义，避免误伤含该词的文本
    if args.schema or (len(args.text or []) == 1 and args.text[0] == "schema"):
        defaults = {
            "output_dir": args.output_dir,
            "debug_port": args.debug_port,
            "chrome": args.chrome,
        }
        if args.profile_dir:
            defaults["profile_dir"] = args.profile_dir
        print(json.dumps(build_schema(defaults), ensure_ascii=False, indent=2))
        return 0

    text = collect_text(args)
    output_dir = args.output_dir
    if not text.strip():
        payload = envelope_error("no_input", ERROR_MESSAGES["no_input"])
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"错误: {ERROR_MESSAGES['no_input']}", file=sys.stderr)
        return 2

    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        payload = envelope_error("download_failed", "无法创建下载目录", detail=f"{exc}")
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"错误: 无法创建下载目录 {output_dir}: {exc}", file=sys.stderr)
        return 1

    def emit(message: str) -> None:
        print(message, file=sys.stderr)

    result, error = run_downloads(
        text,
        output_dir=output_dir,
        port=args.debug_port,
        chrome_path=args.chrome,
        profile_dir=args.profile_dir,
        emit=emit,
    )

    if error is not None:
        data = result.to_data() if result is not None else None
        payload = envelope_error(error.code, error.message, data=data, detail=error.detail)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            if data:
                render_text(result, output_dir)  # type: ignore[arg-type]
            print(f"错误: {error.message}", file=sys.stderr)
        return 2

    assert result is not None
    if args.json:
        if result.failed:
            print(
                json.dumps(
                    envelope_error(
                        "download_failed",
                        f"{result.failed} 条下载失败",
                        data=result.to_data(),
                    ),
                    ensure_ascii=False,
                )
            )
        else:
            print(json.dumps(envelope_ok(result.to_data()), ensure_ascii=False))
    else:
        render_text(result, output_dir)
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
