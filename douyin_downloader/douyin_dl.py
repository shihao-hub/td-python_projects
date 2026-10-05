# /// script
# requires-python = ">=3.12"
# dependencies = ["websocket-client>=1.8", "beautifulsoup4>=4.12", "html2text>=2024.2.26"]
# ///
"""抖音/B 站视频下载器 + 知乎文章提取器（uv 单脚本）。

用法：
    uv run douyin_dl.py "<含抖音/B 站/知乎链接的文本>" [--json] [--output-dir DIR] [--headed]
    Get-Content 文案.txt -Raw | uv run douyin_dl.py --json
    uv run douyin_dl.py --close-browser     # 关掉常驻的自动化 Chrome
    uv run douyin_dl.py schema              # 导出 CLI 契约 JSON（零业务 I/O）

抖音：默认 headless-new 模式——Chrome 新版无头，实测可过抖音风控，屏幕上零痕迹（无窗口、
     无任务栏图标）。若某条 stream_not_found，整批自动改用 background 模式（真 Chrome +
     窗口移到屏幕外 + 静音，不抢焦点也不外放声音）重试一遍。
    抖音为 DASH 音视频分离流：视频流与音频流分别下载，**按时间轴对齐后**经 ffmpeg -c copy
     合并（ffprobe 取 min 时长再 -t 裁齐，避免长的那条在尾部留下静音/冻结帧）；未捕获到
     音频流时降级为无声视频并在结果里标记 audio=missing。下载用的请求头优先复放 CDP 在导航
     时录到的真实头（含 Cookie / 真实 Referer），而非手拼 Referer + 写死 UA。
     --headed 切前台可见窗口（人工过验证滑块）；--headless=old 为旧无头，已被抖音风控识别。
B 站：API + 浏览器登录态路线——复用同一 Chrome profile 的 cookie（SESSDATA），
     未登录时按 bilibili_not_logged_in 失败（首次先 --headed 登录一次 bilibili.com）；
     DASH 音视频分离流经 ffmpeg -c copy 合并，需本机已安装 ffmpeg。
支持裸 BV 号（BV+10 位字母数字）、b23.tv 短链、bilibili.com 链接、多 P（?p=N 或全量下载）。
知乎：真实浏览器 + 登录态打开页面，把回答 / 专栏文章正文原封不动提取为 Markdown + 本地原图
      （`{output_dir}/zhihu/{标题}/article.md` 与 `images/`）。登录态关键 cookie `z_c0` 为
      HttpOnly，只能经 CDP 读取；未登录时按 zhihu_not_logged_in 失败（首次先 --headed 登录
      一次 zhihu.com）。只支持回答（/question/<qid>/answer/<aid>）与专栏文章（/p/<pid>）。

分层（单文件内，遵循《CLI 工具开发标准》的 Service 核心 + 薄壳原则）：
    - 契约层：错误码、JSON 包络、schema 定义（不导入任何业务依赖）
    - 基础设施层：Chrome 启动、CDP 调用、HTTP 下载、ffmpeg 合并（唯一接触外部资源的地方）
    - Service 层：文本提链、链接归一化、批量下载/提取编排（不读写标准流、不退出进程）
    - CLI 适配层：参数解析、人读/JSON 渲染、退出码映射
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import html2text
import websocket
from bs4 import BeautifulSoup

VERSION = "2.6.0"
PROG = "douyin_dl"
# 项目目录名（monorepo 目录名）用下划线，与 CLI 程序名 PROG 区分
PROJECT_DIR_NAME = "douyin_downloader"

# sh-ai-todo: 将整个文件改造成 uv 的单脚本启动文件

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEBUG_PORT = 9222

# Chrome 启动模式。background 为默认：真 Chrome（非无头，规避抖音风控），但窗口
# 窗口移到屏幕外，既不抢焦点也不外放声音。headless 两档仅作备选显式开启。
MODE_BACKGROUND = "background"
MODE_HEADED = "headed"
MODE_HEADLESS_OLD = "headless-old"
MODE_HEADLESS_NEW = "headless-new"
LAUNCH_MODES = (MODE_BACKGROUND, MODE_HEADED, MODE_HEADLESS_OLD, MODE_HEADLESS_NEW)
HEADLESS_MODES = (MODE_HEADLESS_OLD, MODE_HEADLESS_NEW)
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
    "no_douyin_url": "输入文本中没有抖音/B 站/知乎链接，全部已跳过",
    "chrome_launch_failed": "Chrome 启动失败",
    "invalid_url": "链接非法或短链解析失败，未能得到视频/文章 ID",
    "stream_not_found": "未能在页面中捕获视频流（可能需要人工完成验证）",
    "download_failed": "视频流下载失败",
    "bilibili_not_logged_in": "B 站未登录：加 --headed 运行，在弹出的 Chrome 窗口中登录 bilibili.com 后重跑",
    "bilibili_api_error": "B 站接口调用失败",
    "ffmpeg_merge_failed": "音视频合并失败（ffmpeg）",
    "zhihu_not_logged_in": "知乎未登录：加 --headed 运行，在弹出的 Chrome 窗口中登录 zhihu.com 后重跑",
    "zhihu_extract_failed": "知乎正文提取失败（页面结构可能已改版）",
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
        "description": "含抖音/B 站/知乎链接（或裸 BV 号）的文本，可包含多个链接与无关文案",
    }


def _schema_error_property() -> dict[str, Any]:
    return _obj(
        {
            "code": {"type": "string", "enum": sorted(ERROR_MESSAGES)},
            "message": {"type": "string"},
            "detail": {"type": "string"},
        },
        required=["code", "message"],
    )


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
            "audio": {
                "type": "string",
                "enum": ["merged", "included", "missing", "unknown"],
                "description": (
                    "音轨状态（仅成功记录带此字段）：merged=视频+音频按时间轴对齐后经 ffmpeg 合并；"
                    "included=单条流自带音轨无需合并；missing=未捕获到音频流（或视频来自 <video> "
                    "元素兜底、与 DASH 音频不同源而被放弃配对），已降级为无声视频"
                ),
            },
            "error": _schema_error_property(),
        },
        required=["input_url", "video_url", "video_id", "ok", "title", "path", "bytes"],
    )
    extract_item = _obj(
        {
            "input_url": {"type": "string", "description": "文本中提取到的原始知乎链接"},
            "article_url": {"type": "string", "description": "归一化后的知乎页面地址；失败时为空串"},
            "article_id": {"type": "string", "description": "回答 ID（answer/<aid>）或专栏文章 ID（/p/<pid>）；失败时为空串"},
            "ok": {"type": "boolean"},
            "title": {"type": "string", "description": "文章标题清洗后的目录名主干"},
            "path": {"type": "string", "description": "article.md 落盘绝对路径；失败时为空串"},
            "images_total": {"type": "integer", "minimum": 0, "description": "正文中去重后的图片总数"},
            "images_failed": {
                "type": "integer",
                "minimum": 0,
                "description": "原图下载失败的张数（不致命，Markdown 中保留原始 URL 引用）",
            },
            "error": _schema_error_property(),
        },
        required=[
            "input_url",
            "article_url",
            "article_id",
            "ok",
            "title",
            "path",
            "images_total",
            "images_failed",
        ],
    )
    skipped = _obj(
        {
            "url": {"type": "string"},
            "reason": {"type": "string", "enum": ["not_supported"]},
        },
        required=["url", "reason"],
    )
    data = _obj(
        {
            "downloaded": {"type": "array", "items": item},
            "extracted": {
                "type": "array",
                "items": extract_item,
                "description": "知乎回答/专栏文章的提取结果（每篇一条记录）",
            },
            "skipped": {"type": "array", "items": skipped},
            "summary": _obj(
                {
                    "total": {"type": "integer", "minimum": 0, "description": "视频下载记录条数"},
                    "succeeded": {"type": "integer", "minimum": 0},
                    "failed": {"type": "integer", "minimum": 0},
                    "skipped": {"type": "integer", "minimum": 0},
                    "extracted": {"type": "integer", "minimum": 0, "description": "知乎提取记录条数"},
                },
                required=["total", "succeeded", "failed", "skipped", "extracted"],
            ),
        },
        required=["downloaded", "extracted", "skipped", "summary"],
    )
    return _obj(
        {
            "ok": {"type": "boolean"},
            "data": data,
            "error": _schema_error_property(),
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
        "description": "从一段文本中提取抖音/B 站视频链接与知乎回答/专栏文章链接：抖音经 Chrome CDP 捕获无水印视频流并下载，B 站走 API + 浏览器登录态（cookie）路线，知乎经真实浏览器 + 登录态把正文原封不动提取为 Markdown + 本地原图",
        "commands": [
            {
                "name": PROG,
                "summary": "提取文本中的抖音/B 站链接并下载、知乎链接提取为 Markdown（串行逐个处理，按域名自动分流；不支持的链接跳过）",
                "input": {
                    "text": {
                        "type": "array",
                        "items": _schema_text_property(),
                        "description": "位置参数，多段以换行拼接；缺省且 stdin 非 TTY 时读取 stdin，'-' 显式表示 stdin",
                    },
                    "options": {
                        "json": {"type": "boolean", "default": False, "description": "输出 JSON 包络"},
                        "schema": {"type": "boolean", "default": False, "description": "仅输出本契约 JSON 并退出"},
                        "output_dir": {"type": "string", "default": resolved["output_dir"], "description": "下载根目录（视频落其下 douyin/、bilibili/ 子目录，知乎落 zhihu/）"},
                        "debug_port": {"type": "integer", "default": resolved["debug_port"], "description": "Chrome CDP 端口"},
                        "profile_dir": {"type": "string", "default": "", "description": "Chrome 专用 Profile 目录，空表示使用默认数据目录"},
                        "chrome": {"type": "string", "default": resolved["chrome"], "description": "Chrome 可执行文件路径"},
                        "headed": {"type": "boolean", "default": False, "description": "前台可见窗口模式（人工完成验证滑块 / 首次登录 B 站与知乎）"},
                        "headless": {
                            "type": "string",
                            "enum": ["new", "old", "background"],
                            "default": "new",
                            "description": "启动模式：new=Chrome 新版无头（默认，屏幕零痕迹）、old=旧无头（已被抖音风控识别，会 stream_not_found）、background=真 Chrome + 窗口移到屏幕外",
                        },
                        "close_browser": {"type": "boolean", "default": False, "description": "关闭常驻的自动化 Chrome 实例后退出（不处理任何链接）"},
                    },
                },
                "output": {
                    "envelope": response,
                    "success": {"ok": True, "data": "<OutputData>"},
                    "partial_failure": {"ok": False, "data": "<OutputData>", "error": "<Error>"},
                    "failure": {"ok": False, "error": "<Error>", "data": "<OutputData，可能为空集合>"},
                },
                "exit_codes": {
                    "0": "全部链接处理成功",
                    "1": "存在失败项或部分成功",
                    "2": "调用参数错误，或未发现可处理的抖音/B 站/知乎链接",
                },
                "constraints": [
                    "处理串行执行，复用同一个 Chrome 与标签页",
                    "Chrome 默认以 headless-new 模式启动（Chrome 新版无头，实测可过抖音风控且屏幕上无窗口、无任务栏图标）",
                    "headless-new 下某条 stream_not_found 时，整批抖音链接会自动改用 background 模式重试一遍；旧无头 --headless=old 是显式选择，不自动回退",
                    "background 模式为真 Chrome + 窗口移到屏幕外 + 静音，因此屏幕上看不到也不外放声音；--headed 为前台可见窗口（人工过验证滑块 / 登录 B 站与知乎）",
                    "单条失败不中断整批，结果中逐条给出结构化错误",
                    "需要人工完成抖音验证滑块时该条失败，用 --headed 重跑后人工处理",
                    "抖音为 DASH 音视频分离流，下载前按时间轴对齐（ffprobe 取 min 时长后 -t 裁齐，避免尾部静音/冻结帧），经 ffmpeg -c copy 合并；未捕获到音频流时降级为无声视频并在 audio 字段标记 missing",
                    "抖音下载请求头优先复放 CDP 录制的真实请求头（Network.requestWillBeSentExtraInfo，含 Cookie 与真实 Referer），只剔除 range/content-length/content-type/accept-encoding/accept/accept-language；采集失败时退化为真实 UA + 抖音 Referer",
                    "视频流来自 <video> 元素兜底时视为另一路 rendition，不与 DASH 音频配对（避免静默失步），该流自带音轨则记 included，否则记 missing",
                    "B 站链接复用同一 Chrome profile 的登录态（SESSDATA），未登录时该批按 bilibili_not_logged_in 失败；首次使用先 --headed 登录一次 bilibili.com",
                    "B 站视频为 DASH 音视频分离流，下载后经 ffmpeg -c copy 合并，需本机已安装 ffmpeg",
                    "裸 BV 号（BV+10 位字母数字）与 b23.tv 短链、bilibili.com 链接等效支持",
                    "知乎链接只支持回答（/question/<qid>/answer/<aid>）与专栏文章（/p/<pid>）两类，其余知乎链接（想法/收藏夹/问题页等）按 invalid_url 失败",
                    "知乎提取复用同一 Chrome profile 的登录态（关键 cookie z_c0 为 HttpOnly，只能经 CDP 读取），未登录时该批按 zhihu_not_logged_in 失败；首次使用先 --headed 登录一次 zhihu.com",
                    "视频产物按平台分子目录：抖音 {output_dir}/douyin/、B 站 {output_dir}/bilibili/，与知乎的 zhihu/ 对称；同名文件自动加 _1、_2 后缀，不覆盖既有产物",
                    "知乎每篇文章一个独立目录 {output_dir}/zhihu/{标题}/，内含 article.md 与 images/（原图下载，命名 image_001 起）；同名目录自动加 _1、_2 后缀，不覆盖既有产物",
                    "知乎正文图片下载失败不致命：该图在 Markdown 中保留原始 URL 引用，失败张数记入 images_failed",
                    "Chrome 实例运行结束后常驻不退出（复用登录态，后续运行秒连）；用 --close-browser 显式收尾",
                ],
            }
        ],
        "side_effects": {
            "filesystem": [
                resolved["output_dir"],
                os.path.join(resolved["output_dir"], "douyin"),
                os.path.join(resolved["output_dir"], "bilibili"),
                os.path.join(resolved["output_dir"], "zhihu"),
            ],
            "process": [
                "Chrome（独立 Profile + CDP 调试端口，默认 headless-new；回退或显式指定时为 background：真浏览器 + 窗口移到屏幕外）"
            ],
            "network": [
                "抖音页面与 douyinvod.com 音视频流",
                "B 站 API（api.bilibili.com）与 bilivideo CDN 音视频流",
                "知乎页面（www.zhihu.com / zhuanlan.zhihu.com）与 zhimg.com 图片 CDN",
            ],
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


LAUNCH_MODE_MARKER = "launch-mode.txt"


def _write_launch_mode_marker(mode: str) -> None:
    """记录本工具最近一次拉起的 Chrome 模式（无头时 UA 指纹已被覆盖，标记文件更可靠）。"""
    try:
        with open(os.path.join(data_dir(), LAUNCH_MODE_MARKER), "w", encoding="utf-8") as fh:
            fh.write(mode)
    except OSError:
        pass


def chrome_current_mode(port: int) -> str:
    """判定占用端口的 Chrome 处于哪种启动模式：优先读启动标记，UA 仅作兜底判据。"""
    try:
        with open(os.path.join(data_dir(), LAUNCH_MODE_MARKER), "r", encoding="utf-8") as fh:
            marker = fh.read().strip()
        if marker in LAUNCH_MODES:
            return marker
    except OSError:
        pass
    try:
        with urllib.request.urlopen(cdpx_url(port, "/json/version"), timeout=2) as resp:
            info = json.load(resp)
        if "HeadlessChrome" in (info.get("User-Agent") or ""):
            return MODE_HEADLESS_OLD
    except Exception:
        pass
    return MODE_HEADED


def browser_close(port: int = DEBUG_PORT, timeout: float = 10.0) -> bool:
    """向浏览器级 CDP 端点发送 Browser.close，优雅关闭整个 Chrome 实例。"""
    try:
        with urllib.request.urlopen(cdpx_url(port, "/json/version"), timeout=5) as resp:
            info = json.load(resp)
        ws_url = info.get("webSocketDebuggerUrl")
        if not ws_url:
            return False
        ws = websocket.create_connection(ws_url, timeout=timeout)
        try:
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            try:
                while True:
                    res = json.loads(ws.recv())
                    if res.get("id") == 1:
                        break
            except (websocket.WebSocketConnectionClosedException, ConnectionError, OSError):
                pass  # Chrome 关闭时通常直接断开连接，视为已请求成功
        finally:
            ws.close()
        return True
    except Exception:
        return False


def wait_chrome_exit(port: int = DEBUG_PORT, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not chrome_ready(port):
            return True
        time.sleep(0.5)
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
    mode: str = MODE_BACKGROUND,
) -> int:
    """启动独立的 Chrome 自动化实例（持久化 Profile + CDP 调试端口）。

    返回浏览器进程 PID。

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

    ps1 = _build_embedded_ps1(chrome_path, profile, port, url, mode)
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
    _write_launch_mode_marker(mode)
    return int(match.group(1))


def _build_embedded_ps1(
    chrome_path: str,
    profile_dir: str,
    port: int,
    url: str,
    mode: str = MODE_BACKGROUND,
) -> str:
    """生成内嵌 PowerShell 脚本（单引号字符串，内部单引号需翻倍转义）。"""

    def q(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    return (
        "$chrome = " + q(chrome_path) + "\n"
        "$profile = " + q(profile_dir) + "\n"
        "$Url = " + q(url) + "\n"
        f"$port = {port}\n"
        f"$mode = {q(mode)}\n"
        "\n"
        "# Ensure preferences (Downloads folder and developer mode)\n"
        '$prefDir = Join-Path $profile "Default"\n'
        "if (!(Test-Path $prefDir)) {\n"
        "    New-Item -ItemType Directory -Path $prefDir -Force | Out-Null\n"
        "}\n"
        "\n"
        "$flags = ''\n"
        "if ($mode -eq 'headless-old' -or $mode -eq 'headless-new') {\n"
        "    # 显式无头：旧无头已被抖音风控识别（stream_not_found），仅作备选保留\n"
        "    try { $ver = (Get-Item $chrome).VersionInfo.ProductVersion } catch { $ver = '' }\n"
        "    $hl = if ($mode -eq 'headless-new') { ' --headless=new' } else { ' --headless' }\n"
        "    $flags = $hl + ' --mute-audio --autoplay-policy=no-user-gesture-required "
        "--window-size=1380,850 --disable-blink-features=AutomationControlled'\n"
        "    if ($ver) {\n"
        "        $UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/' + $ver + ' Safari/537.36'\n"
        "        $flags = $flags + ' --user-agent=\"' + $UA + '\"'\n"
        "    }\n"
        "}\n"
        "if ($mode -eq 'background') {\n"
        "    # 真 Chrome（非无头，规避风控）+ 静音 + 移到屏幕外 + 禁用遮挡节流。\n"
        "    # 不用 HWND_BOTTOM/WS_EX_NOACTIVATE：实测两者都会让抖音播放器不加载视频流\n"
        "    # （窗口被判定为不可见 → 页面不放流），而移到屏幕外不产生遮挡，Chrome 照常渲染。\n"
        "    $flags = ' --mute-audio --autoplay-policy=no-user-gesture-required'\n"
        "    $flags = $flags + ' --window-position=-32000,-32000 --window-size=800,600'\n"
        "    $flags = $flags + ' --disable-backgrounding-occluded-windows'"
        " + ' --disable-renderer-backgrounding'"
        " + ' --disable-features=CalculateNativeWinOcclusion'\n"
        "}\n"
        "\n"
        '$cmdline = "`"$chrome`" --user-data-dir=`"$profile`" --no-first-run '
        "--no-default-browser-check --remote-debugging-port=$port --remote-allow-origins=*$flags "
        '`"$Url`""\n'
        "\n"
        "$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
        "@{ CommandLine = $cmdline }\n"
        'Write-Output ("ReturnCode: " + $res.ReturnValue + ", ProcessId: " + $res.ProcessId)\n'
        "\n"
        "# background 模式：窗口已在屏幕外，无需再改 z-order/激活属性（那反而会掐断视频流）\n"
    )


def ensure_chrome_running(
    port: int = DEBUG_PORT,
    *,
    chrome_path: str = CHROME_PATH,
    profile_dir: str | None = None,
    download_dir: str | None = None,
    mode: str = MODE_BACKGROUND,
) -> int:
    """确保端口上跑着目标模式的 Chrome，返回浏览器进程 PID。

    已有实例模式一致时返回 0（无需重新拉起）。
    """
    if chrome_ready(port):
        if chrome_current_mode(port) == mode:
            return 0
        print("[*] Chrome running in a different mode; closing it to relaunch...", file=sys.stderr)
        browser_close(port)
        wait_chrome_exit(port)

    print(f"[*] Launching Chrome (mode: {mode})...", file=sys.stderr)
    pid = start_chrome(
        "https://www.douyin.com",
        chrome_path=chrome_path,
        profile_dir=profile_dir,
        port=port,
        download_dir=download_dir,
        mode=mode,
    )

    for _ in range(15):
        time.sleep(1)
        if chrome_ready(port):
            print(f"[+] Chrome CDP ready on port {port}.", file=sys.stderr)
            return pid
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


def cdp_call(ws_url: str, method: str, params: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
    """发送单条 CDP 命令并等待同名响应，返回 result 字段。"""
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        msg_id = int(time.time() * 1000) % 1000000
        ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            res = json.loads(ws.recv())
            if res.get("id") == msg_id:
                if "error" in res:
                    raise RuntimeError(f"CDP {method} 失败: {res['error']}")
                return res.get("result", {})
    finally:
        ws.close()


def cdp_get_cookies(port: int, url: str) -> dict[str, str]:
    """经 CDP Network.getCookies 读取指定 URL 所属域的 cookie（含 HttpOnly），返回 name->value。"""
    pages = [t for t in get_targets(port) if t.get("type") == "page"]
    if not pages:
        raise RuntimeError("无可用 page target，无法读取 cookie")
    result = cdp_call(pages[0]["webSocketDebuggerUrl"], "Network.getCookies", {"urls": [url]})
    return {c["name"]: c["value"] for c in result.get("cookies", [])}


def browser_user_agent(port: int = DEBUG_PORT) -> str:
    """取当前 Chrome 实例的真实 UA；失败回退 USER_AGENT。

    历史实现把 UA 写死成 Chrome/120.0.0.0，与实际浏览器版本不符——签名 CDN 链接常与
    UA 绑定，这种错配是潜在的失败源。
    """
    try:
        with urllib.request.urlopen(cdpx_url(port, "/json/version"), timeout=2) as resp:
            info = json.load(resp)
        ua = (info.get("User-Agent") or "").strip()
    except Exception:
        ua = ""
    return ua or USER_AGENT


# 复放录制到的请求头时剔除的字段：这几个必须由本次请求自己决定，照搬会坏事。
# 前六个与 FetchV 一致（见 research/fetchv-download-implementation.md §3.5 的 l 数组）。
_NETWORK_IGNORED_REQUEST_HEADERS = frozenset(
    {
        "range",
        "if-range",  # 页面的媒体请求常带它（条件 Range）；复放到一次全新的完整下载会导致 304/半截响应
        "content-length",
        "content-type",
        "accept-encoding",
        "accept",
        "accept-language",
        # CDP 额外暴露出、urllib 不能照搬的：Host 由 URL 推导，Connection 是逐跳头。
        "host",
        "connection",
    }
)

# 合法 HTTP 字段名（RFC 9110 token）；CDP 会带出 HTTP/2 伪头（:authority / :method 等），
# 它们以 ':' 开头、不是合法字段名，urllib 会直接抛 ValueError。
_HTTP_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def navigate_and_capture_headers(
    ws_url: str,
    url: str,
    *,
    settle: float = 6.0,
    timeout: float = 30.0,
) -> dict[str, dict[str, str]]:
    """在同一个 CDP 连接里完成「挂 Network 监听 → 导航 → 收集真实请求头」。

    必须在导航之前挂上监听，否则导航触发的请求一个都收不到——这正是把 Page.navigate
    与事件收集放进同一个连接的原因。历史做法是下载时手拼 Referer + 写死 UA，漏掉
    Cookie（抖音的 ttwid / msToken 带签名）就会失稳。

    Cookie 与真实 Referer 只出现在 requestWillBeSentExtraInfo 里（requestWillBeSent 的
    request.headers 会缺 Cookie），故两者按 requestId 合并、以后者为准。

    返回 {完整 URL: {头名: 值}}；收集失败返回空字典，由调用方降级。
    """
    headers_by_url: dict[str, dict[str, str]] = {}
    url_by_request: dict[str, str] = {}
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        ws.send(json.dumps({"id": 101, "method": "Page.navigate", "params": {"url": url}}))
        # 短超时 + 截止时间轮询：事件是零散到达的，不能一次 recv 就收工。
        ws.settimeout(1.0)
        deadline = time.time() + settle
        while time.time() < deadline:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except (websocket.WebSocketConnectionClosedException, OSError):
                break
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "Network.requestWillBeSent":
                request = params.get("request") or {}
                target = request.get("url") or ""
                request_id = params.get("requestId")
                if request_id and target.startswith("http"):
                    url_by_request[request_id] = target
                    base = request.get("headers") or {}
                    if isinstance(base, dict) and base:
                        headers_by_url.setdefault(target, {}).update(base)
            elif method == "Network.requestWillBeSentExtraInfo":
                target = url_by_request.get(params.get("requestId") or "")
                extra = params.get("headers")
                if target and isinstance(extra, dict) and extra:
                    headers_by_url.setdefault(target, {}).update(extra)
    finally:
        ws.close()
    return headers_by_url


def replayable_headers(recorded: dict[str, str] | None) -> dict[str, str]:
    """把录制到的请求头整理成可复放的一整套。

    剔除三类：① 必须由本次请求自决的字段（range/if-range/content-length/…）；
    ② Host / Connection 这类由 URL 或连接决定、照搬必错的字段；
    ③ CDP 带出的 HTTP/2 伪头（以 ':' 开头）及非法字段名——urllib 会抛
    `ValueError: Invalid header name`。

    字段名统一小写：CDP 的 requestWillBeSent 与 requestWillBeSentExtraInfo 大小写不一致
    （Referer vs referer），不归一化会同时留下两份、靠 urllib 的 capitalize() 随机合并。
    字典后写覆盖先写，于是 extraInfo 的值（更权威）胜出。
    """
    if not recorded:
        return {}
    replayable: dict[str, str] = {}
    for name, value in recorded.items():
        lowered = name.lower()
        if lowered in _NETWORK_IGNORED_REQUEST_HEADERS or lowered.startswith(":"):
            continue
        if not _HTTP_TOKEN_RE.match(name):
            continue
        replayable[lowered] = value
    return replayable


def describe_headers(headers: dict[str, str]) -> str:
    """日志用：列出复放的字段名，敏感值只报长度、不打明文（Cookie 绝不落日志）。"""
    sensitive = {"cookie", "authorization", "proxy-authorization", "set-cookie"}
    parts = []
    for name in sorted(headers, key=str.lower):
        if name.lower() in sensitive:
            parts.append(f"{name}=**({len(headers[name])}B)")
        else:
            parts.append(name)
    return ", ".join(parts)


def douyin_request_headers(recorded: dict[str, str] | None, port: int = DEBUG_PORT) -> dict[str, str]:
    """下载抖音流用的请求头：优先复放录制的真实头，否则退化为真实 UA + 抖音 Referer。"""
    replayed = replayable_headers(recorded)
    if replayed:
        return replayed
    return {"User-Agent": browser_user_agent(port), "Referer": "https://www.douyin.com/"}


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


def open_tab(
    target_url: str,
    port: int = DEBUG_PORT,
    host_filter: str = "douyin.com",
    navigate: bool = True,
) -> str:
    """复用已有目标站点标签页，必要时新建，返回该页的 webSocketDebuggerUrl。

    navigate=False 时只保证拿到标签页而不导航：抖音路径需要在导航**之前**挂 Network
    监听，因此导航交给调用方（见 navigate_and_capture_headers）。
    """
    tabs = [t for t in get_targets(port) if t.get("type") == "page" and host_filter in t.get("url", "")]
    if tabs:
        page = tabs[0]
        ws_url = page["webSocketDebuggerUrl"]
        if navigate and target_url not in page.get("url", ""):
            print(f"[*] Navigating page to: {target_url}", file=sys.stderr)
            navigate_page(ws_url, target_url)
            time.sleep(3)
        return ws_url

    create_url = target_url if navigate else "about:blank"
    put_url = cdpx_url(port, f"/json/new?{urllib.parse.quote(create_url, safe='')}")
    req = urllib.request.Request(put_url, method="PUT")
    page = json.load(urllib.request.urlopen(req, timeout=10))
    if navigate:
        time.sleep(3)
    return page["webSocketDebuggerUrl"]


def http_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    """带自定义请求头 GET 并解析 JSON 响应；失败抛出 urllib/OSError/ValueError 异常。"""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def http_get_bytes(url: str, headers: dict[str, str]) -> bytes:
    """带自定义请求头 GET 并返回原始字节（图片等小文件用）。

    非 2xx 由 urllib 抛 HTTPError；不打印进度（与 download_stream 的区别）。
    """
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def unique_dir(parent: str, stem: str) -> str:
    """生成并创建不覆盖既有目录的文章目录（冲突时追加 _1、_2 …），返回其路径。

    与 unique_path 同款防覆盖策略，差别只在唯一化对象是**目录名**（知乎每篇文章一个目录，
    目录内文件名固定为 article.md / images/image_00N.ext，不再逐文件去重）。
    目录本身在这里创建：正文无图时也要能落盘 article.md（不能依赖图片目录的创建）。
    """
    os.makedirs(parent, exist_ok=True)
    candidate = os.path.join(parent, stem)
    index = 1
    while os.path.exists(candidate):
        candidate = os.path.join(parent, f"{stem}_{index}")
        index += 1
    os.makedirs(candidate, exist_ok=True)
    return candidate


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


def download_stream(video_url: str, output_path: str, headers: dict[str, str] | None = None) -> tuple[str, int]:
    """下载视频流到指定路径（调用方保证 output_path 不冲突）。返回 (路径, 字节数)。

    headers 缺省时使用抖音默认头（UA + 抖音 Referer）。
    """
    req = urllib.request.Request(
        video_url,
        headers=headers or {"User-Agent": USER_AGENT, "Referer": "https://www.douyin.com/"},
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


AV_TRIM_TOLERANCE = 0.1  # 秒：两条流时长差小于此值视为已对齐，不值得裁剪


def probe_duration(media_path: str) -> float:
    """用 ffprobe 取媒体时长（秒）；失败或取不到返回 0.0。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                media_path,
            ],
            capture_output=True,
            text=True,
            creationflags=flags,
        )
    except OSError:
        return 0.0
    if proc.returncode != 0:
        return 0.0
    try:
        value = float((proc.stdout or "").strip())
    except ValueError:
        return 0.0
    return value if value > 0 else 0.0


def merge_av_streams(video_path: str, audio_path: str, output_path: str) -> bool:
    """ffmpeg -c copy 合并 DASH 音视频流（无重编码）。

    合并前先按时间轴对齐：两条流时长不等时裁到重叠区间，避免长的那条在尾部留下
    静音 / 冻结帧。思路取自 FetchV 的 audioVideoCopy()——它按 start/end 时间戳把
    两个队列的尾巴裁到重叠区间，见 research/fetchv-download-implementation.md §5。
    整文件合并场景下等价做法是用 ffprobe 取 min(时长) 后交给 ffmpeg -t。

    ffprobe 取不到时长时退化为 -shortest（近似对齐）；两者都失效则按原样合并，
    至少不比历史行为更差。
    """
    video_dur = probe_duration(video_path)
    audio_dur = probe_duration(audio_path)
    overlap = 0.0
    if video_dur and audio_dur and abs(video_dur - audio_dur) > AV_TRIM_TOLERANCE:
        overlap = min(video_dur, audio_dur)

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video_path, "-i", audio_path]
    cmd += ["-t", f"{overlap:.3f}"] if overlap else ["-shortest"]
    cmd += ["-c", "copy", output_path]

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(cmd, capture_output=True, creationflags=flags)
    except OSError:
        return False
    return proc.returncode == 0


def probe_has_audio(media_path: str) -> bool:
    """探测媒体文件是否自带音频流（决定要不要再合并一遍）。

    ffprobe 缺失或异常时返回 False：走正常合并路径，行为与历史一致。
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                media_path,
            ],
            capture_output=True,
            text=True,
            creationflags=flags,
        )
    except OSError:
        return False
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def cleanup_files(*paths: str) -> None:
    """尽力删除指定文件（临时流清理用），不存在或删不掉都静默。"""
    for path in paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Service 层：文本提链、链接归一化、批量下载编排
# --------------------------------------------------------------------------

_URL_RE = re.compile(r"""https?://[^\s<>"'`（）()【】\[\]{}，。；、]+""", re.IGNORECASE)
_TRAILING = "\"'`.,;:!?，。；：！？、）)】」》>"
_DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")
_BILI_HOSTS = ("bilibili.com", "b23.tv")
_ZHIHU_HOSTS = ("zhihu.com",)
_BV_RE = re.compile(r"\bBV[0-9A-Za-z]{10}\b")


@dataclass
class SkippedLink:
    url: str
    reason: str


@dataclass
class ExtractResult:
    douyin: list[str] = field(default_factory=list)
    bilibili: list[str] = field(default_factory=list)
    zhihu: list[str] = field(default_factory=list)
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
    audio: str = ""
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
        if self.ok:
            payload["audio"] = self.audio or "unknown"
        else:
            payload["error"] = {
                "code": self.error_code,
                "message": self.error_message,
                "detail": self.error_detail,
            }
        return payload


@dataclass
class ExtractRecord:
    """单篇知乎文章的提取结果（成功或结构化失败）。"""

    input_url: str
    ok: bool
    article_url: str = ""
    article_id: str = ""
    title: str = ""
    path: str = ""
    images_total: int = 0
    images_failed: int = 0
    error_code: str = ""
    error_message: str = ""
    error_detail: str = ""

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "input_url": self.input_url,
            "article_url": self.article_url,
            "article_id": self.article_id,
            "ok": self.ok,
            "title": self.title,
            "path": self.path,
            "images_total": self.images_total,
            "images_failed": self.images_failed,
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
    extracted: list[ExtractRecord] = field(default_factory=list)
    skipped: list[SkippedLink] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.records if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.records if not r.ok)

    @property
    def extracted_succeeded(self) -> int:
        return sum(1 for r in self.extracted if r.ok)

    @property
    def extracted_failed(self) -> int:
        return sum(1 for r in self.extracted if not r.ok)

    def to_data(self) -> dict[str, Any]:
        return {
            "downloaded": [r.to_json() for r in self.records],
            "extracted": [r.to_json() for r in self.extracted],
            "skipped": [{"url": s.url, "reason": s.reason} for s in self.skipped],
            "summary": {
                "total": len(self.records),
                "succeeded": self.succeeded,
                "failed": self.failed,
                "skipped": len(self.skipped),
                "extracted": len(self.extracted),
            },
        }


def is_douyin_url(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    host = host.lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return any(host == base or host.endswith("." + base) for base in _DOUYIN_HOSTS)


def is_bilibili_url(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    host = host.lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return any(host == base or host.endswith("." + base) for base in _BILI_HOSTS)


def is_zhihu_url(url: str) -> bool:
    """zhihu.com 域判断：www / zhuanlan / 裸域均命中，模式与 is_bilibili_url 一致。"""
    host = urllib.parse.urlsplit(url).hostname or ""
    host = host.lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return any(host == base or host.endswith("." + base) for base in _ZHIHU_HOSTS)


def extract_links(text: str) -> ExtractResult:
    """从任意文本中提取链接：抖音/B 站/知乎链接按出现顺序去重收集，其余记为 skipped。

    文本中的裸 BV 号（不在任何已收集 URL 内）归一化为 B 站视频页地址后进入 bilibili 队列。
    """
    result = ExtractResult()
    seen: set[str] = set()
    urls: list[str] = []
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(_TRAILING)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if is_douyin_url(url):
            result.douyin.append(url)
        elif is_bilibili_url(url):
            result.bilibili.append(url)
        elif is_zhihu_url(url):
            result.zhihu.append(url)
        else:
            result.skipped.append(SkippedLink(url=url, reason="not_supported"))

    for bvid in _BV_RE.findall(text or ""):
        if any(bvid in u for u in urls):
            continue
        normalized = f"https://www.bilibili.com/video/{bvid}"
        if normalized in seen:
            continue
        seen.add(normalized)
        result.bilibili.append(normalized)
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


# ------------------------- B 站 Service -------------------------


def bili_headers(cookie_header: str = "") -> dict[str, str]:
    """B 站 API 与 CDN 下载通用请求头（Referer 必带，否则 CDN 403）。"""
    headers = {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def extract_bvid(text: str) -> tuple[str, int]:
    """从 URL 或裸文本中提取 (bvid, page)。page 取 ?p=N 参数，无则 0。"""
    match = re.search(r"/video/(BV[0-9A-Za-z]{10})", text) or re.search(r"\b(BV[0-9A-Za-z]{10})\b", text)
    bvid = match.group(1) if match else ""
    p_match = re.search(r"[?&]p=(\d+)", text)
    return bvid, (int(p_match.group(1)) if p_match else 0)


def resolve_bilibili_url(url: str) -> tuple[str, int]:
    """解析 B 站输入：b23.tv 短链跟随重定向后提取，其余直接提取。返回 (bvid, page)。"""
    final_url = url
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host == "b23.tv":
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
        try:
            with opener.open(req, timeout=15) as resp:
                resolved = resp.geturl()
                if resolved:
                    final_url = resolved
        except (urllib.error.URLError, OSError, ValueError):
            final_url = url
    return extract_bvid(final_url)


def fetch_bili_view(bvid: str, cookie_header: str) -> dict[str, Any]:
    """获取 B 站视频元信息（标题、pages 多 P 列表）。API 失败抛 bilibili_api_error。"""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        payload = http_get_json(url, bili_headers(cookie_header))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise AppError("bilibili_api_error", ERROR_MESSAGES["bilibili_api_error"], f"view 请求失败: {exc}")
    if payload.get("code") != 0:
        raise AppError(
            "bilibili_api_error",
            ERROR_MESSAGES["bilibili_api_error"],
            f"view code={payload.get('code')} {payload.get('message')}",
        )
    return payload.get("data") or {}


def fetch_bili_playurl(bvid: str, cid: Any, cookie_header: str) -> dict[str, Any]:
    """获取 B 站播放地址（DASH 优先，qn=127 请求最高档，服务端按登录权益下发）。"""
    url = (
        f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
        "&qn=127&fnval=16&fnver=0&platform=pc"
    )
    try:
        payload = http_get_json(url, bili_headers(cookie_header))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise AppError("bilibili_api_error", ERROR_MESSAGES["bilibili_api_error"], f"playurl 请求失败: {exc}")
    if payload.get("code") != 0:
        raise AppError(
            "bilibili_api_error",
            ERROR_MESSAGES["bilibili_api_error"],
            f"playurl code={payload.get('code')} {payload.get('message')}",
        )
    return payload.get("data") or {}


def pick_bili_streams(playurl: dict[str, Any]) -> tuple[str, str, str]:
    """从 playurl 数据选流：dash.video 取 id 最大者（同 id 取先出现，B 站 avc1 优先排列），
    dash.audio 取首项（B 站按质量降序，dolby/flac 在独立字段）。返回 (kind, video_url, audio_url)，
    kind 为 "dash" 或 "durl"（老视频单流直链，audio_url 为空串）。"""
    dash = playurl.get("dash")
    if isinstance(dash, dict) and dash.get("video"):
        videos = dash["video"]
        best = videos[0]
        for v in videos:
            if v.get("id", 0) > best.get("id", 0):
                best = v
        audio_list = dash.get("audio") or []
        audio_url = audio_list[0].get("baseUrl", "") if audio_list else ""
        return "dash", best.get("baseUrl", ""), audio_url
    durl = playurl.get("durl") or []
    if durl:
        return "durl", durl[0].get("url", ""), ""
    raise AppError("stream_not_found", ERROR_MESSAGES["stream_not_found"])


def clean_title(title: str, fallback: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|\r\n\t]', "_", title or "")[:50].strip(" ._")
    return cleaned or fallback


_JS_DOUYIN_RESOURCES = (
    "performance.getEntriesByType('resource').map(r => r.name)"
    ".filter(n => n.includes('.douyinvod.com'))"
)

_JS_VIDEO_SRC = (
    "Array.from(document.querySelectorAll('video')).map(v => v.currentSrc || v.src).filter(Boolean)"
)

# 视频流来源：dash = 资源记录里的 DASH 成对流（与 media-audio 同属一份 manifest）；
# element = <video> 元素 src 兜底（实测常指向另一路 rendition，与 DASH 音频不同源）。
VIDEO_SOURCE_DASH = "dash"
VIDEO_SOURCE_ELEMENT = "element"


def page_title(ws_url: str, attempts: int = 8, interval: float = 1.0) -> str:
    """轮询取页面标题（抖音是 SPA，标题在详情接口返回后才写入，读一次会拿到空串）。

    取不到时返回空串，由调用方用 douyin_<视频ID> 兜底——不因为标题没就绪就丢掉文件名。
    """
    for _ in range(attempts):
        try:
            title = str(eval_cdp(ws_url, "document.title") or "").strip()
        except Exception:
            title = ""
        if title:
            return title
        time.sleep(interval)
    return ""


def capture_stream_urls(ws_url: str, attempts: int = 12, interval: float = 1.0) -> tuple[str, str, str]:
    """在页面中轮询捕获 (视频流地址, 音频流地址, 视频来源)。

    抖音是 DASH 音视频分离：`media-video-*` 与 `media-audio-*` 是两条独立流，只取其一
    必然残缺（历史 bug：这里曾用 `!n.includes('media-audio')` 主动滤掉音频，导致抖音
    视频永远无声）。两者同属一份 manifest，时间轴天然对齐，下载后经 ffmpeg 合并。

    优先级：资源记录里的 DASH 成对流 > `<video>` 元素 src（仅作视频兜底）。返回的视频
    来源供调用方判断能否与 DASH 音频配对——混用两路 rendition 会造成静默失步。
    音频缺失不算失败，由调用方降级处理。
    """
    video_url = ""
    audio_url = ""
    video_source = ""
    for _ in range(attempts):
        resources = eval_cdp(ws_url, _JS_DOUYIN_RESOURCES) or []
        audio = [u for u in resources if "media-audio" in u]
        video = [u for u in resources if "media-audio" not in u and "/video/tos/" in u]
        if video:
            video_url = video[-1]
            video_source = VIDEO_SOURCE_DASH
        if audio:
            audio_url = audio[-1]
        if video_url and audio_url:
            return video_url, audio_url, video_source

        if not video_url:
            srcs = eval_cdp(ws_url, _JS_VIDEO_SRC) or []
            for src in srcs:
                if src.startswith("http") and not src.startswith("blob:"):
                    video_url = src
                    video_source = VIDEO_SOURCE_ELEMENT
                    break

        time.sleep(interval)
    return video_url, audio_url, video_source


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

        # 导航与请求头采集必须在同一个 CDP 连接里：监听晚于导航就一个请求头都收不到。
        emit(f"[*] Navigating page to: {video_url}")
        recorded: dict[str, dict[str, str]] = {}
        try:
            recorded = navigate_and_capture_headers(tab_ws_url, video_url)
        except Exception as exc:  # 采集失败不致命：退化为 UA + Referer 手拼
            emit(f"[!] 请求头采集失败（{type(exc).__name__}: {exc}），退化为默认头。")

        video_stream, audio_stream, video_source = capture_stream_urls(tab_ws_url)
        if not video_stream:
            raise AppError("stream_not_found", ERROR_MESSAGES["stream_not_found"])

        # 标题在抓到流之后再读：此时页面必然已加载完成。抖音是 SPA，标题晚于
        # 详情接口返回才写入，之前在此处读一次会周期性拿到空串、文件名退化成视频 ID。
        title = page_title(tab_ws_url)

        if video_source == VIDEO_SOURCE_ELEMENT and audio_stream:
            # <video> 元素 src 常指向另一路 rendition，与 DASH 音频不同源；配对会造成
            # 静默失步（肉眼看不出来），所以宁可明确降级为无声，也不混源。
            emit(
                "[!] 视频流来自 <video> 元素兜底（另一路 rendition），与 DASH 音频不同源，"
                "已放弃配对以避免静默失步；该流若自带音轨会直接采用。"
            )
            audio_stream = ""

        video_headers = douyin_request_headers(recorded.get(video_stream), port)
        audio_headers = douyin_request_headers(recorded.get(audio_stream), port) if audio_stream else {}
        emit(
            f"[*] 复放请求头 {len(video_headers)} 个"
            f"（来源：{'CDP 录制' if replayable_headers(recorded.get(video_stream)) else '默认 UA + Referer'}）"
            f" {describe_headers(video_headers)}"
        )

        stem = clean_title(str(title), f"douyin_{record.video_id}")
        output_path = unique_path(output_dir, stem)
        emit(f"[+] Video stream: {video_stream[:90]}...")
        if audio_stream:
            emit(f"[+] Audio stream: {audio_stream[:90]}...")
        else:
            emit("[!] 未捕获到音频流，将降级为无声视频（音画完整性无法保证）。")

        video_tmp = output_path + ".video.mp4"
        audio_tmp = output_path + ".audio.m4a"
        try:
            saved_path, _ = download_stream(video_stream, video_tmp, video_headers)
            if probe_has_audio(video_tmp):
                # 兜底源本身已是合流，直接落盘：再合并一次会出双音轨
                os.replace(video_tmp, output_path)
                record.audio = "included"
            elif audio_stream:
                emit("[*] Downloading audio stream...")
                download_stream(audio_stream, audio_tmp, audio_headers)
                emit("[*] Merging with ffmpeg...")
                if not merge_av_streams(video_tmp, audio_tmp, output_path):
                    raise AppError("ffmpeg_merge_failed", ERROR_MESSAGES["ffmpeg_merge_failed"])
                record.audio = "merged"
            else:
                os.replace(video_tmp, output_path)
                record.audio = "missing"
        except Exception:
            cleanup_files(video_tmp, audio_tmp, output_path)
            raise
        cleanup_files(video_tmp, audio_tmp)

        size = os.path.getsize(output_path)
        record.ok = True
        record.title = stem
        record.path = output_path
        record.bytes = size
        emit(f"[+] Download complete: {output_path} ({size} bytes, audio={record.audio})")
    except AppError as exc:
        record.error_code = exc.code
        record.error_message = exc.message
        record.error_detail = exc.detail
    except Exception as exc:  # 基础设施异常统一归类，不向用户抛裸栈
        record.error_code = "download_failed"
        record.error_message = ERROR_MESSAGES["download_failed"]
        record.error_detail = f"{type(exc).__name__}: {exc}"
    return record


def download_bilibili_one(
    input_url: str,
    *,
    cookie_header: str,
    output_dir: str,
    emit: Callable[[str], None],
) -> list[DownloadRecord]:
    """处理单个 B 站输入（链接或裸 BV 号）：view → playurl → 选流 → 下载 → ffmpeg 合并。

    多 P 视频返回多条记录（每 P 一条）；顶层失败归为单条失败记录，保证单条不中断整批。
    """
    try:
        bvid, page = resolve_bilibili_url(input_url)
        if not bvid:
            raise AppError("invalid_url", ERROR_MESSAGES["invalid_url"], f"解析结果: {input_url}")

        view = fetch_bili_view(bvid, cookie_header)
        pages_all = view.get("pages") or []
        if not pages_all:
            pages_all = [{"page": 1, "cid": view.get("cid")}]

        if page:
            targets = [p for p in pages_all if p.get("page") == page]
            if not targets:
                raise AppError(
                    "invalid_url",
                    ERROR_MESSAGES["invalid_url"],
                    f"分 P {page} 不存在（共 {len(pages_all)} P）: {bvid}",
                )
        else:
            targets = pages_all
        multi = len(targets) > 1

        title = clean_title(str(view.get("title") or ""), f"bilibili_{bvid}")
        records: list[DownloadRecord] = []
        for p_info in targets:
            page_no = p_info.get("page") or 1
            stem = f"{title}_P{page_no}" if multi else title
            record = DownloadRecord(
                input_url=input_url,
                ok=False,
                video_url=f"https://www.bilibili.com/video/{bvid}?p={page_no}",
                video_id=f"{bvid}_p{page_no}",
                title=stem,
            )
            records.append(record)
            video_tmp = ""
            audio_tmp = ""
            try:
                playurl = fetch_bili_playurl(bvid, p_info.get("cid"), cookie_header)
                kind, video_url, audio_url = pick_bili_streams(playurl)
                if not video_url:
                    raise AppError("stream_not_found", ERROR_MESSAGES["stream_not_found"])

                if kind == "dash":
                    if not audio_url:
                        raise AppError("stream_not_found", ERROR_MESSAGES["stream_not_found"])
                    output_path = unique_path(output_dir, stem)
                    video_tmp = output_path + ".video.m4s"
                    audio_tmp = output_path + ".audio.m4s"
                    emit(f"[*] Downloading video stream (P{page_no})...")
                    download_stream(video_url, video_tmp, bili_headers(cookie_header))
                    emit(f"[*] Downloading audio stream (P{page_no})...")
                    download_stream(audio_url, audio_tmp, bili_headers(cookie_header))
                    emit(f"[*] Merging with ffmpeg (P{page_no})...")
                    if not merge_av_streams(video_tmp, audio_tmp, output_path):
                        raise AppError("ffmpeg_merge_failed", ERROR_MESSAGES["ffmpeg_merge_failed"])
                    record.audio = "merged"
                    cleanup_files(video_tmp, audio_tmp)
                else:
                    # durl 老视频单流直链（FLV/MP4），无需合并
                    suffix = ".flv" if ".flv" in video_url.split("?")[0] else ".mp4"
                    output_path = unique_path(output_dir, stem, suffix)
                    download_stream(video_url, output_path, bili_headers(cookie_header))
                    record.audio = "included"

                record.ok = True
                record.path = output_path
                record.bytes = os.path.getsize(output_path)
                emit(f"[+] Download complete: {output_path}")
            except AppError as exc:
                cleanup_files(video_tmp, audio_tmp)
                record.error_code = exc.code
                record.error_message = exc.message
                record.error_detail = exc.detail
            except Exception as exc:  # 基础设施异常统一归类，不向用户抛裸栈
                cleanup_files(video_tmp, audio_tmp)
                record.error_code = "download_failed"
                record.error_message = ERROR_MESSAGES["download_failed"]
                record.error_detail = f"{type(exc).__name__}: {exc}"
        return records
    except AppError as exc:
        return [
            DownloadRecord(
                input_url=input_url,
                ok=False,
                error_code=exc.code,
                error_message=exc.message,
                error_detail=exc.detail,
            )
        ]
    except Exception as exc:
        return [
            DownloadRecord(
                input_url=input_url,
                ok=False,
                error_code="download_failed",
                error_message=ERROR_MESSAGES["download_failed"],
                error_detail=f"{type(exc).__name__}: {exc}",
            )
        ]


# ------------------------- 知乎 Service -------------------------

_ZHIHU_BASE = "https://www.zhihu.com"
_ZHIHU_REFERER = "https://www.zhihu.com/"
# 正文容器候选（新版专栏/回答 → 老版回答 → 通用富文本），按顺序取第一个非空容器
ZHIHU_CONTAINER_SELECTORS = (".Post-RichTextContainer", ".RichContent-inner", ".RichText")
_ZHIHU_SELECTORS_JS = json.dumps(list(ZHIHU_CONTAINER_SELECTORS))

# 正文容器内的无关壳元素（互动条、广告、图标等）：DOM→Markdown 前先剥掉，避免把
# 「赞同/评论/收起」这类按钮文字混进正文
_ZHIHU_STRIP_SELECTORS = (
    "script",
    "style",
    "noscript",
    "button",
    "iframe",
    "svg",
    ".ContentItem-actions",
    ".RichContent-actions",
    ".Post-Sub",
    ".RichText-ad",
    ".Advertisement",
    ".VoteButton",
    ".Reward",
    ".FollowButton",
    ".ContentItem-time",
)

# 图片原图候选属性，优先级从左到右（data-original 常是小图缩略版，故 data-actualsrc 优先）
_ZHIHU_IMAGE_ATTRS = ("data-actualsrc", "data-original", "srcset", "src")
_ZHIHU_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp")
_ZHIHU_DEFAULT_IMAGE_SUFFIX = ".jpg"

# 正文容器出现与否的探测脚本（诊断信息随结果带回，便于定位知乎改版；href 用于复核落点）
_ZHIHU_PROBE_TEMPLATE = """
(() => {
  const sels = __SELECTORS__;
  const stats = [];
  let chosen = null;
  for (const sel of sels) {
    const nodes = Array.from(document.querySelectorAll(sel));
    const nonEmpty = nodes.filter((n) => (n.innerText || '').trim().length > 0);
    stats.push({ selector: sel, matched: nodes.length, nonEmpty: nonEmpty.length });
    if (!chosen && nonEmpty.length) chosen = sel;
  }
  return JSON.stringify({ found: !!chosen, selector: chosen, title: document.title || '',
                          href: document.location.href, stats: stats });
})()
"""

# 正文提取脚本：只负责「滚动触发懒加载 + 取容器 innerHTML」，DOM→Markdown 交给 bs4 + html2text。
# 必须返回 Promise（eval_cdp 用 awaitPromise 等待分段滚动结束）。
# 滚动阶段用**时间预算**而非固定步数：图片多的长文页面高度会随懒加载不断增长，后台标签页的
# 定时器还会被 Chrome 节流（setTimeout 最小 1s），固定步数会把 CDP 调用拖到超时。
_ZHIHU_EXTRACT_TEMPLATE = """
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const sels = __SELECTORS__;
  const pick = () => {
    for (const sel of sels) {
      const nodes = Array.from(document.querySelectorAll(sel))
        .filter((node) => (node.innerText || '').trim().length > 0);
      if (nodes.length) return nodes[0];
    }
    return null;
  };
  const pageHeight = () => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
  // 知乎正文图片是懒加载：不滚到底，img 的 src 仍是占位图（data-actualsrc 一般已在初始 HTML 里）
  const deadline = Date.now() + __SCROLL_BUDGET_MS__;
  let lastY = -1;
  while (Date.now() < deadline) {
    if (window.scrollY + window.innerHeight >= pageHeight() - 4) break;
    window.scrollTo(0, window.scrollY + Math.max(400, Math.floor(window.innerHeight * 0.9)));
    await sleep(__SCROLL_STEP_MS__);
    if (window.scrollY === lastY) break;  // 滚不动（滚动容器不是 window / 已到底）：不再空转
    lastY = window.scrollY;
  }
  window.scrollTo(0, 0);
  await sleep(200);
  const node = pick();
  if (!node) {
    return JSON.stringify({ found: false, title: document.title || '', author: '', html: '' });
  }
  const authorNode = document.querySelector('.AuthorInfo-name, .Post-Author .AuthorInfo-name');
  return JSON.stringify({
    found: true,
    title: document.title || '',
    author: authorNode ? (authorNode.innerText || '').trim() : '',
    html: node.innerHTML,
  });
})()
"""

_ZHIHU_PROBE_JS = _ZHIHU_PROBE_TEMPLATE.replace("__SELECTORS__", _ZHIHU_SELECTORS_JS)
ZHIHU_EXTRACT_JS = (
    _ZHIHU_EXTRACT_TEMPLATE.replace("__SELECTORS__", _ZHIHU_SELECTORS_JS)
    .replace("__SCROLL_BUDGET_MS__", "20000")
    .replace("__SCROLL_STEP_MS__", "250")
)

ZHIHU_CONTAINER_ATTEMPTS = 30  # 正文容器轮询次数（1s 间隔 → 30s 超时）
ZHIHU_CONTAINER_INTERVAL = 1.0
# 分段滚动 20s 预算 + 回顶/取 HTML 余量，给到 120s；超时说明页面异常（长文/后台节流），
# 由 extract_zhihu_article 转成带诊断的 zhihu_extract_failed
ZHIHU_EXTRACT_TIMEOUT = 120.0


def resolve_zhihu_url(url: str) -> tuple[str, str]:
    """归一化知乎输入：回答取 aid、专栏文章取 pid 作为 article_id。

    知乎没有短链形态，不需要跟随重定向，article_url 即原 URL；两类之外的知乎链接
    （想法 /pin/、收藏夹、问题页、个人主页等）按 invalid_url 失败。
    """
    match = re.search(r"/answer/(\d+)", url) or re.search(r"/p/(\d+)", url)
    if not match:
        raise AppError(
            "invalid_url",
            ERROR_MESSAGES["invalid_url"],
            f"仅支持回答（/question/<qid>/answer/<aid>）与专栏文章（/p/<pid>）: {url}",
        )
    return url, match.group(1)


def zhihu_cookies(port: int) -> dict[str, str]:
    """读知乎域 cookie（含 HttpOnly 的 z_c0）；读取失败返回空字典，由调用方转结构化错误。"""
    try:
        return cdp_get_cookies(port, _ZHIHU_BASE)
    except Exception:
        return {}


def check_zhihu_login(port: int) -> bool:
    """登录门：z_c0 存在即已登录（知乎登录态关键 cookie，HttpOnly，只能经 CDP 读取）。"""
    return bool(zhihu_cookies(port).get("z_c0"))


def zhihu_headers(cookie_header: str = "") -> dict[str, str]:
    """知乎图片下载请求头（Referer 必带，zhimg 图片 CDN 校验来源）。"""
    headers = {"User-Agent": USER_AGENT, "Referer": _ZHIHU_REFERER}
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


# 知乎在有未读消息时会把计数写进 document.title（形如「(11 条消息) 标题」），
# 这是站点自己加的运行期前缀，不属于文章标题，落盘前必须剥掉
_ZHIHU_TITLE_PREFIX_RE = re.compile(r"^\(\s*\d+\s*条消息\s*\)\s*")


def clean_zhihu_title(title: str) -> str:
    """清洗知乎页面标题：剥掉「(N 条消息)」未读计数前缀与末尾「 - 知乎」站点后缀。"""
    cleaned = _ZHIHU_TITLE_PREFIX_RE.sub("", (title or "").strip())
    return re.sub(r"\s*[-–—|]\s*知乎\s*$", "", cleaned).strip()


def _srcset_best(value: str) -> str:
    """从 srcset 里挑清晰度最高的候选（2x 像素密度按 ×1000 折算，优先级高于 1440w）。"""
    best_url = ""
    best_score = -1.0
    for part in (value or "").split(","):
        fields = part.strip().split()
        if not fields or not fields[0]:
            continue
        score = 1.0
        if len(fields) > 1:
            descriptor = fields[1].strip().lower()
            try:
                if descriptor.endswith("w"):
                    score = float(descriptor[:-1])
                elif descriptor.endswith("x"):
                    score = float(descriptor[:-1]) * 1000
            except ValueError:
                score = 1.0
        if score > best_score:
            best_url, best_score = fields[0], score
    return best_url


def _zhihu_image_url(tag: Any) -> str:
    """按 data-actualsrc → data-original → srcset 最大候选 → src 取原图 URL 并绝对化。

    知乎公式图的 src 是相对路径（/equation?tex=...），必须绝对化后才能下载；
    data: 开头的占位图（内联 svg / 1x1 gif）一律跳过。
    """
    candidates: list[str] = []
    for attr in ("data-actualsrc", "data-original"):
        value = (tag.get(attr) or "").strip()
        if value:
            candidates.append(value)
    srcset = _srcset_best(tag.get("srcset") or "")
    if srcset:
        candidates.append(srcset)
    src = (tag.get("src") or "").strip()
    if src:
        candidates.append(src)
    for value in candidates:
        if value.startswith("data:"):
            continue
        absolute = urllib.parse.urljoin(_ZHIHU_BASE, value)
        if absolute.startswith("http"):
            return absolute
    return ""


def collect_zhihu_image_urls(html: str) -> list[str]:
    """提取正文 HTML 里的原图 URL 清单（绝对化 + 去重，保持出现顺序）。"""
    soup = BeautifulSoup(html or "", "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("img"):
        url = _zhihu_image_url(tag)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _image_suffix(url: str) -> str:
    """从 URL 路径推断图片扩展名，白名单外一律 .jpg（含知乎公式 /equation?tex= 这类无后缀 URL）。"""
    path = urllib.parse.urlsplit(url).path.lower()
    for suffix in _ZHIHU_IMAGE_SUFFIXES:
        if path.endswith(suffix):
            return suffix
    return _ZHIHU_DEFAULT_IMAGE_SUFFIX


def download_zhihu_images(
    image_urls: list[str],
    images_dir: str,
    cookie_header: str,
    emit: Callable[[str], None] = lambda _msg: None,
) -> dict[str, str]:
    """下载正文原图到 images_dir，返回「绝对 URL → 本地文件名」映射。

    命名按出现顺序 image_001.ext 起（编号含失败项，保证同一 URL 始终对应同一编号）；
    单张失败不致命：捕获后该 URL 不进入映射，Markdown 中会保留其原始 URL 引用。
    """
    mapping: dict[str, str] = {}
    if not image_urls:
        return mapping
    os.makedirs(images_dir, exist_ok=True)
    headers = zhihu_headers(cookie_header)
    for index, url in enumerate(image_urls, start=1):
        filename = f"image_{index:03d}{_image_suffix(url)}"
        try:
            data = http_get_bytes(url, headers)
            if not data:
                raise OSError("响应为空")
            with open(os.path.join(images_dir, filename), "wb") as fh:
                fh.write(data)
        except Exception as exc:
            emit(f"[!] 图片下载失败（{url[:100]}）：{type(exc).__name__}: {exc}，保留原始 URL 引用")
            continue
        mapping[url] = filename
    return mapping


def clean_zhihu_html(html: str, images_map: dict[str, str]) -> str:
    """把知乎正文 HTML 清洗成可转换形态：剥壳 → 图注斜体化 → 图片本地化。

    images_map 命中时 src 换成相对路径 images/image_00N.ext（与 article.md 同级）；
    未命中（下载失败）时保留绝对原图 URL；无有效 URL 的占位图整段丢弃。
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for selector in _ZHIHU_STRIP_SELECTORS:
        for node in soup.select(selector):
            node.decompose()
    for caption in soup.select("figcaption"):
        text = caption.get_text(" ", strip=True)
        caption.name = "p"
        caption.clear()
        emphasis = soup.new_tag("em")
        emphasis.string = text
        caption.append(emphasis)
    for index, tag in enumerate(soup.find_all("img"), start=1):
        url = _zhihu_image_url(tag)
        for attr in _ZHIHU_IMAGE_ATTRS + (
            "class",
            "loading",
            "data-rawwidth",
            "data-rawheight",
            "data-caption",
            "data-size",
        ):
            if tag.has_attr(attr):
                del tag[attr]
        local = images_map.get(url, "")
        if local:
            tag["src"] = f"images/{local}"
        elif url:
            tag["src"] = url
        else:
            tag.decompose()  # 纯占位图（data: URL）：无内容可保留
            continue
        if not (tag.get("alt") or "").strip():
            tag["alt"] = f"图{index}"
    return str(soup)


# html2text 有两处「英文向」的补空格启发式，对中文会产出与网页不一致的空格：
#   ① 开 <em>/<i> 时，前一字符既非空白也非 **ASCII** 标点就补空格（中文标点不在
#      string.punctuation 里，「、」后也被补）；
#   ② </em>/</strong> 后，下一字符不属于「][(){} 空白 .!?」就补空格（汉字/中文标点命中）。
# 两处只对英文成立——中文里 * 可以紧贴汉字开闭强调。下面的子类按「相邻字符是否汉字/中文
# 标点」精准跳过，ASCII 场景仍走父类原逻辑（不改变英文转换行为）。
_CJK_CHAR_RE = re.compile(
    r"[\u2018\u2019\u201c\u201d\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)


class ZhihuHtml2Text(html2text.HTML2Text):
    """html2text 的中文修正版：强调标记紧贴汉字/中文标点时不补多余空格。"""

    def handle_tag(self, tag: str, attrs: dict[str, str], start: bool) -> None:
        if (
            tag in ("em", "i", "u")
            and start
            and self.preceding_data
            and _CJK_CHAR_RE.match(self.preceding_data[-1])
        ):
            # 末尾字符是汉字/中文标点 → HTML 里本就没有空格（真有空格时末尾是空白，
            # 父类也不会补），故临时改成 ASCII 标点让父类判定「前面是标点」而不补空格
            original = self.preceding_data
            self.preceding_data = original[:-1] + "."
            try:
                super().handle_tag(tag, attrs, start)
            finally:
                self.preceding_data = original
            return
        super().handle_tag(tag, attrs, start)

    def handle_data(self, data: str, entity_char: bool = False) -> None:
        if self.preceding_stressed and data and _CJK_CHAR_RE.match(data[0]):
            self.preceding_stressed = False  # 走「不补空格」分支，由父类照常输出文本
        super().handle_data(data, entity_char)


def render_zhihu_markdown(
    title: str,
    author: str,
    article_url: str,
    clean_html: str,
    fetched_at: str,
) -> str:
    """清洗后 HTML → Markdown 文本（html2text），附标题与来源元信息引用块。"""
    converter = ZhihuHtml2Text()
    converter.body_width = 0  # 不按宽度硬换行，否则中文段落会被拦腰截断
    converter.ignore_images = False  # 图片保留为 ![alt](images/image_00N.ext)
    converter.ignore_emphasis = False
    # 强调标记用 *：CommonMark 下 _ 夹在中文之间不生效（中文属「词内」，_ 不能开闭强调），
    # 知乎正文的中文斜体/加粗必须用 * 才能被正确渲染
    converter.emphasis_mark = "*"
    # single_line_break 保持默认 False：<p>/<div> 之间的空行即段落分隔，中文分段靠它保住
    converter.unicode_snob = True  # 保留中文标点原字符，不做 ASCII 近似替换
    body = re.sub(r"\n{3,}", "\n\n", converter.handle(clean_html or "")).strip()
    header = "\n".join(
        [
            f"# {title}",
            "",
            f"> 来源: {article_url}",
            f"> 作者: {author or '未知'}",
            f"> 提取时间: {fetched_at}",
        ]
    )
    return f"{header}\n\n{body}\n" if body else f"{header}\n"


def extract_zhihu_article(ws_url: str, url: str, article_id: str) -> tuple[str, str, str]:
    """导航到知乎页面并提取正文，返回 (标题, 作者, 正文 HTML)。

    轮询正文容器就绪（30 × 1s）→ 执行 ZHIHU_EXTRACT_JS（滚动懒加载后取 innerHTML）。
    容器始终不出现时抛 zhihu_extract_failed，detail 带页面标题与候选区统计，便于定位知乎改版。
    标题取不到时留空，由调用方以 article_id 兜底；作者缺失不致命。
    """
    navigate_page(ws_url, url)
    probe: dict[str, Any] = {}
    for _ in range(ZHIHU_CONTAINER_ATTEMPTS):
        try:
            raw = eval_cdp(ws_url, _ZHIHU_PROBE_JS)
            probe = json.loads(raw) if isinstance(raw, str) and raw else {}
        except Exception as exc:
            probe = {"error": f"{type(exc).__name__}: {exc}"}
        if probe.get("found"):
            break
        time.sleep(ZHIHU_CONTAINER_INTERVAL)
    else:
        detail = (
            f"页面标题: {probe.get('title') or '(空)'}；"
            f"正文候选区统计: {json.dumps(probe.get('stats') or [], ensure_ascii=False)}"
        )
        if probe.get("error"):
            detail += f"；探测异常: {probe['error']}"
        raise AppError("zhihu_extract_failed", ERROR_MESSAGES["zhihu_extract_failed"], detail)

    # 不存在的回答/文章会被知乎重定向（如 /p/1 → 首页），而首页里也有别处的 .RichText：
    # 只认「容器存在」会「提取成功」出一段无关内容，故按落点 URL 里的 article_id 复核。
    href = str(probe.get("href") or "")
    if href and not re.search(rf"/(?:answer|p)/{re.escape(article_id)}(?:\D|$)", href):
        raise AppError(
            "zhihu_extract_failed",
            ERROR_MESSAGES["zhihu_extract_failed"],
            f"页面已重定向到 {href}（未包含目标文章 ID {article_id}），该链接可能已失效",
        )

    try:
        raw = eval_cdp(ws_url, ZHIHU_EXTRACT_JS, timeout=ZHIHU_EXTRACT_TIMEOUT)
    except Exception as exc:
        raise AppError(
            "zhihu_extract_failed",
            ERROR_MESSAGES["zhihu_extract_failed"],
            f"提取脚本执行失败（{type(exc).__name__}: {exc}）；"
            f"页面标题: {probe.get('title') or '(空)'}；命中容器: {probe.get('selector') or '(无)'}",
        ) from exc
    payload = json.loads(raw) if isinstance(raw, str) and raw else {}
    if not payload.get("found"):
        raise AppError(
            "zhihu_extract_failed",
            ERROR_MESSAGES["zhihu_extract_failed"],
            f"提取阶段正文容器消失；页面标题: {payload.get('title') or probe.get('title') or '(空)'}",
        )
    title = clean_zhihu_title(page_title(ws_url)) or clean_zhihu_title(str(payload.get("title") or ""))
    author = str(payload.get("author") or "").strip()
    return title, author, str(payload.get("html") or "")


def extract_zhihu_one(
    input_url: str,
    *,
    output_dir: str,
    port: int,
    mode: str,
    emit: Callable[[str], None],
) -> ExtractRecord:
    """处理单条知乎链接：登录门 → 正文提取 → 原图下载 → 清洗转换 → Markdown 落盘。

    捕获全部异常并转为结构化错误，保证单条失败不中断整批。
    """
    record = ExtractRecord(input_url=input_url, ok=False)
    article_dir = ""
    try:
        article_url, article_id = resolve_zhihu_url(input_url)
        record.article_url = article_url
        record.article_id = article_id

        if not check_zhihu_login(port):
            # 只在 headed 下把登录页摆到用户面前；无头/后台模式用户看不到窗口，擅自打开
            # 知乎页面只会白白触发一次匿名访问，故仅给指引
            if mode == MODE_HEADED:
                try:
                    open_tab(_ZHIHU_BASE, port, "zhihu.com")
                    emit("[*] 知乎未登录：已在 Chrome 窗口打开 zhihu.com，请完成登录后重跑本命令。")
                except Exception as exc:
                    emit(f"[!] 打开 zhihu.com 登录页失败：{type(exc).__name__}: {exc}")
            else:
                emit("[*] 知乎未登录：请加 --headed 重跑，在弹出的前台窗口中登录 zhihu.com。")
            raise AppError("zhihu_not_logged_in", ERROR_MESSAGES["zhihu_not_logged_in"])

        cookie_header = "; ".join(f"{name}={value}" for name, value in zhihu_cookies(port).items())
        # navigate=False：导航交给 extract_zhihu_article，避免 open_tab 先导航一次、这里再导航一次
        tab_ws_url = open_tab(article_url, port, "zhihu.com", navigate=False)
        title, author, body_html = extract_zhihu_article(tab_ws_url, article_url, article_id)
        emit(f"[*] 已取到正文（作者: {author or '未知'}，标题: {title or article_id}）。")

        stem = clean_title(title, f"zhihu_{article_id}")
        article_dir = unique_dir(os.path.join(output_dir, "zhihu"), stem)
        image_urls = collect_zhihu_image_urls(body_html)
        emit(f"[*] 正文图片 {len(image_urls)} 张，下载原图到 {os.path.join(article_dir, 'images')} ...")
        images_map = download_zhihu_images(
            image_urls, os.path.join(article_dir, "images"), cookie_header, emit=emit
        )

        record.images_total = len(image_urls)
        record.images_failed = len(image_urls) - len(images_map)
        clean_html = clean_zhihu_html(body_html, images_map)
        fetched_at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        markdown = render_zhihu_markdown(title, author, article_url, clean_html, fetched_at)
        md_path = os.path.join(article_dir, "article.md")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(markdown)

        record.ok = True
        record.title = stem
        record.path = md_path
        emit(
            f"[+] 提取完成: {md_path}"
            f"（图片 {len(images_map)}/{len(image_urls)}，Markdown {len(markdown)} 字符）"
        )
    except AppError as exc:
        record.error_code = exc.code
        record.error_message = exc.message
        record.error_detail = exc.detail
    except Exception as exc:  # 基础设施异常统一归类，不向用户抛裸栈
        record.error_code = "zhihu_extract_failed"
        record.error_message = ERROR_MESSAGES["zhihu_extract_failed"]
        record.error_detail = f"{type(exc).__name__}: {exc}"
    if not record.ok and article_dir:
        shutil.rmtree(article_dir, ignore_errors=True)  # 半成品目录不留残骸（article.md 未写成）
    return record


def run_downloads(
    text: str,
    *,
    output_dir: str,
    port: int,
    chrome_path: str = CHROME_PATH,
    profile_dir: str | None = None,
    mode: str = MODE_BACKGROUND,
    emit: Callable[[str], None] = lambda _msg: None,
) -> tuple[RunResult | None, AppError | None]:
    """编排：提取链接 → 启动/复用 Chrome → 串行逐条处理（抖音 CDP 嗅探 / B 站 API+cookie / 知乎浏览器提取）→ 汇总。

    返回 (结果, 错误)：入口级错误（无链接、Chrome 启动失败）时结果可能为 None 或已含 skipped。
    """
    extracted = extract_links(text)
    result = RunResult(records=[], skipped=extracted.skipped)

    if not extracted.douyin and not extracted.bilibili and not extracted.zhihu:
        code = "no_douyin_url" if result.skipped else "no_url"
        return (result if result.skipped else None), AppError(code, ERROR_MESSAGES[code])

    try:
        ensure_chrome_running(
            port,
            chrome_path=chrome_path,
            profile_dir=profile_dir,
            download_dir=output_dir,
            mode=mode,
        )
    except AppError as exc:
        return result, exc

    # 产物按平台分子目录（与知乎的 zhihu/ 对称）；子目录由下载层 unique_path 自动创建
    douyin_dir = os.path.join(output_dir, "douyin")
    bilibili_dir = os.path.join(output_dir, "bilibili")

    def run_douyin_batch(urls: list[str], label: str = "") -> list[DownloadRecord]:
        records: list[DownloadRecord] = []
        for index, url in enumerate(urls, start=1):
            emit(f"[*] {label}({index}/{len(urls)}) {url}")
            try:
                # navigate=False：导航连同请求头采集一起交给 download_one，
                # 否则这里先导航一次，Network 监听就挂晚了、收不到任何请求头。
                tab_ws_url = open_tab(resolve_video_url(url), port, "douyin.com", navigate=False)
            except AppError as exc:
                records.append(
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
                records.append(
                    DownloadRecord(
                        input_url=url,
                        ok=False,
                        error_code="chrome_launch_failed",
                        error_message=ERROR_MESSAGES["chrome_launch_failed"],
                        error_detail=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue

            records.append(
                download_one(
                    url,
                    output_dir=douyin_dir,
                    port=port,
                    tab_ws_url=tab_ws_url,
                    emit=emit,
                )
            )
        return records

    result.records.extend(run_douyin_batch(extracted.douyin))

    # 新版无头被拦时自动回退 background（真 Chrome）重试一遍：默认模式追求屏幕零痕迹，
    # 但不能因此丢掉能下到的视频。旧无头是显式选择，不回退（回退会让该 flag 失去意义）。
    if mode == MODE_HEADLESS_NEW:
        failed_stream = [
            r for r in result.records if not r.ok and r.error_code == "stream_not_found"
        ]
        if failed_stream:
            emit(
                f"[!] 新版无头未捕获到流（{len(failed_stream)} 条），"
                "自动回退 background 模式（真 Chrome，窗口移到屏幕外）重试..."
            )
            try:
                browser_close(port)
                wait_chrome_exit(port)
                ensure_chrome_running(
                    port,
                    chrome_path=chrome_path,
                    profile_dir=profile_dir,
                    download_dir=output_dir,
                    mode=MODE_BACKGROUND,
                )
            except AppError as exc:
                emit(f"[!] 回退 background 失败：{exc.message}（保留原始失败结果）")
            else:
                retried = run_douyin_batch(
                    [r.input_url for r in failed_stream], label="回退 "
                )
                merged: list[DownloadRecord] = []
                cursor = 0
                for record in result.records:
                    if not record.ok and record.error_code == "stream_not_found":
                        merged.append(retried[cursor])
                        cursor += 1
                    else:
                        merged.append(record)
                result.records = merged

    # B 站队列：登录门（SESSDATA 校验）→ 串行 API 下载（复用同一 Chrome profile 的 cookie）
    if extracted.bilibili:
        bili_cookies: dict[str, str] = {}
        try:
            # headed 模式下打开 bilibili.com 供人工登录；headless 下仅作为 cookie 读取的 page target
            open_tab("https://www.bilibili.com", port, "bilibili.com")
            bili_cookies = cdp_get_cookies(port, "https://www.bilibili.com")
        except Exception as exc:
            for url in extracted.bilibili:
                result.records.append(
                    DownloadRecord(
                        input_url=url,
                        ok=False,
                        error_code="chrome_launch_failed",
                        error_message=ERROR_MESSAGES["chrome_launch_failed"],
                        error_detail=f"{type(exc).__name__}: {exc}",
                    )
                )

        if bili_cookies and "SESSDATA" not in bili_cookies:
            if mode == MODE_HEADED:
                emit("[*] B 站未登录：已在 Chrome 窗口打开 bilibili.com，请完成登录后重跑。")
            elif mode == MODE_BACKGROUND:
                emit("[*] B 站未登录：请加 --headed 重跑，在弹出的前台窗口中登录 bilibili.com。")
            for url in extracted.bilibili:
                result.records.append(
                    DownloadRecord(
                        input_url=url,
                        ok=False,
                        error_code="bilibili_not_logged_in",
                        error_message=ERROR_MESSAGES["bilibili_not_logged_in"],
                    )
                )
            bili_cookies = {}

        if bili_cookies:
            cookie_header = "; ".join(f"{name}={value}" for name, value in bili_cookies.items())
            total_bili = len(extracted.bilibili)
            for index, url in enumerate(extracted.bilibili, start=1):
                emit(f"[*] (B站 {index}/{total_bili}) {url}")
                result.records.extend(
                    download_bilibili_one(
                        url, cookie_header=cookie_header, output_dir=bilibili_dir, emit=emit
                    )
                )

    # 知乎队列：登录门（z_c0 校验）→ 真实浏览器提取正文 → 原图下载 → Markdown 落盘
    if extracted.zhihu:
        total_zhihu = len(extracted.zhihu)
        for index, url in enumerate(extracted.zhihu, start=1):
            emit(f"[*] (知乎 {index}/{total_zhihu}) {url}")
            result.extracted.append(
                extract_zhihu_one(url, output_dir=output_dir, port=port, mode=mode, emit=emit)
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
        description=(
            "从文本中提取抖音/B 站视频链接并下载、知乎回答/专栏文章链接提取为 Markdown"
            "（按域名自动分流；不支持的链接跳过；多链接串行逐个处理）"
        ),
        epilog=(
            "示例：\n"
            f"  uv run {PROG}.py \"8.74 复制打开抖音 https://v.douyin.com/EBgtkB68340/\"\n"
            f"  uv run {PROG}.py \"BV1B6YR6gEyd 或 https://b23.tv/ApmE1Nd\"\n"
            f"  uv run {PROG}.py \"https://www.zhihu.com/question/1923534024288236685/answer/2021258227166319271\"\n"
            f"  Get-Content 文案.txt -Raw | uv run {PROG}.py --json\n"
            f"  uv run {PROG}.py schema"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="含抖音/B 站/知乎链接或裸 BV 号的文本（多段以换行拼接；缺省且 stdin 非 TTY 时读 stdin，'-' 显式表示 stdin）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 包络（stdout 仅一个 JSON 对象）")
    parser.add_argument("--schema", action="store_true", help="仅输出 CLI 契约 JSON 并退出")
    parser.add_argument(
        "--output-dir",
        default=default_download_dir(),
        help="下载根目录（默认 ~/Downloads；产物按平台落子目录 douyin/、bilibili/、zhihu/）",
    )
    parser.add_argument("--debug-port", type=int, default=DEBUG_PORT, help=f"Chrome CDP 端口（默认 {DEBUG_PORT}）")
    parser.add_argument("--profile-dir", default=None, help="Chrome 专用 Profile 目录（默认项目数据目录下 chrome-profile）")
    parser.add_argument("--chrome", default=CHROME_PATH, help="Chrome 可执行文件路径")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--headed",
        action="store_true",
        help="前台可见窗口模式（人工完成验证滑块 / 首次登录 B 站与知乎）",
    )
    mode_group.add_argument(
        "--headless",
        nargs="?",
        const="new",
        choices=["new", "old", "background"],
        default=None,
        help=(
            "显式指定启动模式：new=Chrome 新版无头（默认，屏幕零痕迹）、"
            "old=旧无头（已被抖音风控识别，会 stream_not_found）、"
            "background=真 Chrome + 窗口移到屏幕外；省略该参数等同 new"
        ),
    )
    parser.add_argument(
        "--close-browser",
        action="store_true",
        help="关闭常驻的自动化 Chrome 实例后退出（不处理任何链接）",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {VERSION}")
    return parser


def resolve_mode(args: argparse.Namespace) -> str:
    """CLI 参数 → 启动模式。

    默认 headless-new：Chrome 新版无头，实测可过抖音风控且屏幕上零痕迹。
    它被拦时 run_downloads 会自动回退 background（真 Chrome + 窗口移到屏幕外）重试一遍。
    """
    if args.headed:
        return MODE_HEADED
    if args.headless == "old":
        return MODE_HEADLESS_OLD
    if args.headless == "background":
        return MODE_BACKGROUND
    return MODE_HEADLESS_NEW


def render_text(result: RunResult, output_dir: str, mode: str = MODE_BACKGROUND) -> None:
    for record in result.records:
        if record.ok:
            print(f"[OK]   {record.video_url}")
            print(f"       文件: {record.path} ({record.bytes} bytes, audio={record.audio})")
            if record.audio == "missing":
                print("       ⚠ 未捕获到音频流：该文件无声，音画完整性无法保证。")
        else:
            print(f"[FAIL] {record.input_url}")
            print(f"       原因: {record.error_code} - {record.error_message}")
            if record.error_detail:
                print(f"       详情: {record.error_detail}")
    for record in result.extracted:
        if record.ok:
            print(f"[OK]   {record.article_url}")
            print(
                f"       文件: {record.path}"
                f"（图片 {record.images_total - record.images_failed}/{record.images_total}）"
            )
        else:
            print(f"[FAIL] {record.input_url}")
            print(f"       原因: {record.error_code} - {record.error_message}")
            if record.error_detail:
                print(f"       详情: {record.error_detail}")
    for skipped in result.skipped:
        print(f"[SKIP] {skipped.url}（不支持的链接）")
    print(
        f"汇总: 视频 {len(result.records)} 条（成功 {result.succeeded}，失败 {result.failed}），"
        f"知乎 {len(result.extracted)} 篇（成功 {result.extracted_succeeded}，失败 {result.extracted_failed}），"
        f"跳过 {len(result.skipped)}；输出目录 {output_dir}"
    )
    if any(record.error_code == "zhihu_not_logged_in" for record in result.extracted):
        print(
            "提示: 知乎需要登录态。加 --headed 重跑，在弹出的 Chrome 窗口中登录 zhihu.com 后再重跑。",
            file=sys.stderr,
        )
    elif result.extracted_failed:
        print(
            "提示: 知乎提取失败多为页面结构改版或风控；错误详情里带页面标题与正文候选区统计，可据此定位。",
            file=sys.stderr,
        )
    if result.failed and not result.succeeded:
        if mode == MODE_HEADLESS_NEW:
            print(
                "提示: 新版无头与 background 回退都没拿到流，抖音多半在要求人工验证："
                "加 --headed 重跑并在弹出的窗口中完成验证。",
                file=sys.stderr,
            )
        elif mode == MODE_HEADLESS_OLD:
            print(
                "提示: 旧无头已被抖音风控识别。去掉 --headless=old 用默认模式重跑，"
                "或加 --headed 人工完成验证。",
                file=sys.stderr,
            )
        elif mode == MODE_BACKGROUND:
            print(
                "提示: Chrome 窗口已移到屏幕外（看不到、不抢焦点）。"
                "若其中出现验证滑块，请加 --headed 重跑并在前台窗口内人工完成。",
                file=sys.stderr,
            )
        else:
            print("提示: 若 Chrome 窗口出现验证滑块，请人工完成后重跑。", file=sys.stderr)


def describe_failures(result: RunResult) -> str:
    """部分成功时的错误摘要（抖音/B 站下载与知乎提取分别计数）。"""
    parts = []
    if result.failed:
        parts.append(f"{result.failed} 条视频下载失败")
    if result.extracted_failed:
        parts.append(f"{result.extracted_failed} 篇知乎提取失败")
    return "，".join(parts) or "存在失败项"


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

    # 收尾分支：关掉常驻的自动化 Chrome，不处理任何链接
    if args.close_browser:
        if not chrome_ready(args.debug_port):
            print(f"未发现常驻 Chrome 实例（端口 {args.debug_port} 无响应）。")
            return 0
        browser_close(args.debug_port)
        if wait_chrome_exit(args.debug_port):
            print(f"已关闭常驻 Chrome 实例（端口 {args.debug_port}）。")
            return 0
        print(f"未能确认 Chrome 退出（端口 {args.debug_port}）。", file=sys.stderr)
        return 1

    mode = resolve_mode(args)

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
        mode=mode,
        emit=emit,
    )

    if error is not None:
        data = result.to_data() if result is not None else None
        payload = envelope_error(error.code, error.message, data=data, detail=error.detail)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            if data:
                render_text(result, output_dir, mode=mode)  # type: ignore[arg-type]
            print(f"错误: {error.message}", file=sys.stderr)
        return 2

    assert result is not None
    failure_total = result.failed + result.extracted_failed
    if args.json:
        if failure_total:
            print(
                json.dumps(
                    envelope_error(
                        "download_failed",
                        describe_failures(result),
                        data=result.to_data(),
                    ),
                    ensure_ascii=False,
                )
            )
        else:
            print(json.dumps(envelope_ok(result.to_data()), ensure_ascii=False))
    else:
        render_text(result, output_dir, mode=mode)
    return 1 if failure_total else 0


if __name__ == "__main__":
    sys.exit(main())
