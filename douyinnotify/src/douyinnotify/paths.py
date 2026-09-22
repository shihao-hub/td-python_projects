"""运行时数据目录解析。

仓库强约束：自有数据文件只放 %APPDATA%\\language_projects\\douyinnotify\\，
取不到 APPDATA 时回退 ~/.language_projects/douyinnotify/；
写入前自动创建完整目录链（含 language_projects 一层）。
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "douyinnotify"


def get_data_dir() -> Path:
    """返回运行时数据目录，写入前自动创建完整目录链。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        root = Path(appdata) / "language_projects" / APP_NAME
    else:
        root = Path.home() / ".language_projects" / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_config_path() -> Path:
    """config.json：watchlist 与检查频率。"""
    return get_data_dir() / "config.json"


def get_state_path() -> Path:
    """state.json：每博主已见 aweme_id 集合与上次检查时间。"""
    return get_data_dir() / "state.json"


def get_browser_profile_dir() -> Path:
    """无头 Chrome 专用 profile 目录（持久化 ttwid，降低弹窗频率）。"""
    return get_data_dir() / "chrome_profile"
