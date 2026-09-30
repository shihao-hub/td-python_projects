"""写前进程检查（Windows tasklist；FR-6/NFR-1）。

仅 ``--apply`` 强制要求 Zed/OpenCode 进程退出；dry-run 只读快照安全，
允许进程运行。非 Windows 平台跳过并返回空（调用方附 note）。
"""

from __future__ import annotations

import subprocess
import sys

from .errors import ProcessRunningError

# 写库前必须退出的进程关键字（tasklist 首列名匹配，大小写不敏感）
WRITE_BLOCKING_KEYWORDS = ("opencode", "zed")


def find_running(keywords: tuple[str, ...] = WRITE_BLOCKING_KEYWORDS) -> list[str]:
    """返回正在运行的相关进程名列表；非 Windows 平台返回空。

    匹配规则：进程名（去 .exe）精确等于关键字或以其开头加 ``-``/``_``
    （如 ``zed-editor``），避免 ``zedhub.exe`` 被 ``zed`` 子串误伤——
    daemon 自身绝不能被自己的写前检查拦截（自锁死）。
    """
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    running: list[str] = []
    for line in out.splitlines():
        first = line.split('","', 1)[0].strip('"').lower()
        if not first:
            continue
        stem = first[:-4] if first.endswith(".exe") else first
        for kw in keywords:
            if stem == kw or stem.startswith(kw + "-") or stem.startswith(kw + "_"):
                running.append(first)
                break
    return running


def assert_writable(processes: list[str]) -> None:
    """写库前置检查：相关进程仍在运行即拒绝（不猜测竞态写入的后果）。"""
    if processes:
        raise ProcessRunningError(
            "检测到以下进程正在运行，请完全关闭后重试: " + ", ".join(sorted(set(processes)))
        )
