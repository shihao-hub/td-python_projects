"""日志：写入 %APPDATA%\\language_projects\\douyinnotify\\logs\\（回退 ~/.language_projects/）。

按天轮转（TimedRotatingFileHandler），保留最近 14 份；
无人值守的计划任务场景下没有控制台，日志是唯一的排障依据。
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler

from .paths import APP_NAME, get_data_dir

LOG_FILENAME = "douyinnotify.log"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化包级 logger（重复调用不叠加 handler）。"""
    logger = logging.getLogger(APP_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    log_dir = get_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)  # FileHandler 不会自动创建父目录
    handler = TimedRotatingFileHandler(
        log_dir / LOG_FILENAME,
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
