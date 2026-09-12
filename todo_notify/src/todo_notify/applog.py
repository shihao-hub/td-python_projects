"""日志：写入 %APPDATA%\\language_projects\\todo_notify\\（回退 ~/.language_projects/todo_notify/）。

按天轮转（TimedRotatingFileHandler），保留最近 14 份；
无人值守的计划任务场景下没有控制台，日志是唯一的排障依据。
"""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

APP_NAME = "todo_notify"
LOG_FILENAME = "todo_notify.log"


def get_log_dir() -> Path:
    """返回日志目录，写入前自动创建完整目录链（含 language_projects 一层）。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        root = Path(appdata) / "language_projects" / APP_NAME
    else:
        root = Path.home() / ".language_projects" / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化包级 logger（重复调用不叠加 handler）。"""
    logger = logging.getLogger(APP_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    handler = TimedRotatingFileHandler(
        get_log_dir() / LOG_FILENAME,
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
