"""Windows 任务计划程序注册 / 卸载早晚两个定时任务。

从 Python subprocess 以列表参数调 schtasks 走 CreateProcess，
规避 Git Bash 下 schtasks 的 MSYS 路径转义问题。

/TR 直接指向 venv 内 todonotify.exe：单路径无空格好引号、启动快、无 uv 开销；
代价是删过 venv 后需 `uv sync` 重新生成 exe 并重装任务（README 已注明）。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("todo_notify")

# 早晚两个计划任务：09:00 与 20:00
SCHEDULES = {
    "todo_notify_morning": "09:00",
    "todo_notify_evening": "20:00",
}


class SchedulerError(RuntimeError):
    """计划任务注册 / 卸载失败。"""


def _exe_path() -> Path:
    """定位当前 venv 内的 todonotify.exe。"""
    exe = Path(sys.executable).with_name("todonotify.exe")
    if not exe.exists():
        raise SchedulerError(
            f"未找到 {exe}，请先在项目目录执行 `uv sync` 后重试"
        )
    return exe


def install() -> list[str]:
    """注册早晚两个每日计划任务，返回成功创建的任务名列表。"""
    tr = f"{_exe_path()} --notify"
    created: list[str] = []
    for task_name, start_time in SCHEDULES.items():
        result = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/F",  # 已存在则覆盖，保证可重复安装
                "/TN",
                task_name,
                "/TR",
                tr,
                "/SC",
                "DAILY",
                "/ST",
                start_time,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            logger.error("schtasks 创建 %s 失败: %s", task_name, result.stderr)
            raise SchedulerError(f"创建计划任务 {task_name} 失败: {result.stderr.strip()}")
        created.append(task_name)
    return created


def _task_exists(task_name: str) -> bool:
    """查询计划任务是否存在（schtasks /Query 退出码 0 即存在）。"""
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode == 0


def uninstall() -> list[str]:
    """卸载两个计划任务，返回成功删除的任务名列表（不存在视作无需删除）。"""
    removed: list[str] = []
    for task_name in SCHEDULES:
        if not _task_exists(task_name):
            continue
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            logger.error("schtasks 删除 %s 失败: %s", task_name, result.stderr)
            raise SchedulerError(f"删除计划任务 {task_name} 失败: {result.stderr.strip()}")
        removed.append(task_name)
    return removed
