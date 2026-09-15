"""路径与运行环境管理。

强约束：所有运行时数据只允许放在 `%APPDATA%\\language_projects\\llm-finetune-lab\\`，
取不到 APPDATA 时回退 `~/.language_projects/llm-finetune-lab/`。

任何 HuggingFace 相关库的导入都必须发生在本模块 `bootstrap()` 调用之后，
否则 HF_HOME 不会生效（库在导入时读取环境变量）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PROJECT_NAME = "llm-finetune-lab"

# 数据根目录可用环境变量覆盖（测试或特殊部署时使用）
DATA_DIR_ENV = "LLMFT_DATA_DIR"

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_data_root() -> Path:
    """解析数据根目录：显式覆盖 > APPDATA > 家目录回退。"""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "language_projects" / PROJECT_NAME
    return Path.home() / ".language_projects" / PROJECT_NAME


DATA_ROOT = resolve_data_root()
CORPUS_DIR = DATA_ROOT / "corpus"
HF_CACHE_DIR = DATA_ROOT / "hf_cache"
DATASETS_DIR = DATA_ROOT / "datasets"
OUTPUTS_DIR = DATA_ROOT / "outputs"
LOGS_DIR = DATA_ROOT / "logs"

ALL_DIRS = (DATA_ROOT, CORPUS_DIR, HF_CACHE_DIR, DATASETS_DIR, OUTPUTS_DIR, LOGS_DIR)

_bootstrapped = False


def ensure_dirs() -> Path:
    """创建全部数据目录（含 language_projects 一层），返回数据根目录。"""
    for directory in ALL_DIRS:
        directory.mkdir(parents=True, exist_ok=True)
    return DATA_ROOT


def configure_hf_env() -> None:
    """把 HuggingFace 缓存指到数据目录；必须在导入 HF 库之前调用。"""
    os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def bootstrap() -> Path:
    """统一入口：建目录 + 配置 HF 环境变量，幂等。"""
    global _bootstrapped
    ensure_dirs()
    configure_hf_env()
    _bootstrapped = True
    return DATA_ROOT


def free_space_text() -> str:
    """数据目录所在磁盘的剩余空间，方便判断模型下载预算。"""
    usage = shutil.disk_usage(str(DATA_ROOT))
    return f"{usage.free / 1024**3:.1f} GB 可用 / 共 {usage.total / 1024**3:.1f} GB"
