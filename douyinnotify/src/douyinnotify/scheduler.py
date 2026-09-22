"""Windows 任务计划程序注册 / 卸载定时检查任务。

从 Python subprocess 以列表参数调 schtasks 走 CreateProcess，
规避 Git Bash 下 schtasks 的 MSYS 路径转义问题。

/SC HOURLY /MO <check_interval_hours>，间隔读 config.json（默认 6 小时）；
/TR 直接指向 venv 内 douyinnotify.exe（路径无空格，README 已注明：
删过 venv 后需 `uv sync` 重新生成 exe 并重装任务）。

按用户决策：开发过程中不注册常驻任务——install_schedule 只提供能力，
全部开发完毕后在交付收尾时才真正执行。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from . import watchlist
from .paths import get_config_path

logger = logging.getLogger("douyinnotify")

TASK_NAME = "douyinnotify_check"


class SchedulerError(RuntimeError):
    """计划任务注册 / 卸载失败。"""


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    """执行 schtasks 子命令，输出解码为 UTF-8；不弹任何控制台窗口。"""
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _task_command() -> str:
    """构造任务命令行：pythonw 运行模块入口，交互会话零弹窗。

    console exe 被计划任务在交互会话运行会弹控制台窗口；
    pythonw 无控制台，stdout 失效由 cli._print 容错兜底，排障靠日志。
    """
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        raise SchedulerError(
            f"未找到 {pythonw}，请先在项目目录执行 `uv sync` 后重试"
        )
    return f'"{pythonw}" -m douyinnotify --notify'


def task_exists(task_name: str = TASK_NAME) -> bool:
    """查询计划任务是否存在（schtasks /Query 退出码 0 即存在）。"""
    return _run_schtasks(["/Query", "/TN", task_name]).returncode == 0


def install() -> tuple[int, str]:
    """注册计划任务，返回 (间隔小时数, 起始时间)。

    /SC HOURLY /MO <check_interval_hours> /ST <schedule_start>，
    如 9 点开始每 6 小时（09:00 / 15:00 / 21:00 / 03:00）；
    已存在时用 /F 覆盖，保证重复安装幂等（改配置后重跑即生效）。
    注意：本函数只提供注册能力，是否调用由交付收尾阶段决定。
    """
    config = watchlist.load_or_init(get_config_path())
    interval = max(1, min(23, config.check_interval_hours))  # schtasks HOURLY 上限 23

    if task_exists():
        logger.info("计划任务 %s 已存在，覆盖更新", TASK_NAME)
    result = _run_schtasks(
        [
            "/Create",
            "/F",  # 已存在则覆盖，保证可重复安装
            "/TN",
            TASK_NAME,
            "/TR",
            _task_command(),  # pythonw 隐藏窗口 + 定时推送
            "/SC",
            "HOURLY",
            "/MO",
            str(interval),
            "/ST",
            config.schedule_start,
        ]
    )
    if result.returncode != 0:
        logger.error("schtasks 创建 %s 失败: %s", TASK_NAME, result.stderr)
        raise SchedulerError(f"创建计划任务 {TASK_NAME} 失败: {result.stderr.strip()}")
    logger.info(
        "已注册计划任务 %s（%s 起每 %d 小时）", TASK_NAME, config.schedule_start, interval
    )
    return interval, config.schedule_start


def uninstall() -> bool:
    """卸载计划任务，返回是否实际删除（不存在视作无需删除，幂等）。"""
    if not task_exists():
        return False
    result = _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0:
        logger.error("schtasks 删除 %s 失败: %s", TASK_NAME, result.stderr)
        raise SchedulerError(f"删除计划任务 {TASK_NAME} 失败: {result.stderr.strip()}")
    logger.info("已删除计划任务 %s", TASK_NAME)
    return True
