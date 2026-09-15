"""基座模型登记表：统一别名 → HuggingFace 仓库 id 的映射。

模型文件有两种来源：
1. HF 缓存（snapshot_download / hf-mirror）—— 直接以 repo id 加载；
2. 本地目录（ModelScope 等手动下载）—— 落在 <数据根>/models/<名字>/，
   加载时经 `source()` 自动优先使用本地目录。
"""

from __future__ import annotations

from pathlib import Path

from apps.common import paths

# 别名：HF 仓库 id（Qwen3 指令版自带 chat template，适合 SFT）
MODELS: dict[str, str] = {
    "qwen3-0.6b": "Qwen/Qwen3-0.6B",
    "qwen3-1.7b": "Qwen/Qwen3-1.7B",
    "qwen3-4b": "Qwen/Qwen3-4B",
}

# 参数量（用于显存预算表的理论估算）
PARAM_COUNTS: dict[str, float] = {
    "qwen3-0.6b": 0.6e9,
    "qwen3-1.7b": 1.7e9,
    "qwen3-4b": 4.0e9,
}


def resolve(alias_or_repo: str) -> tuple[str, str]:
    """别名或原始 repo id → (别名, repo_id)。未知字符串若含 '/' 按 repo id 处理。"""
    if alias_or_repo in MODELS:
        return alias_or_repo, MODELS[alias_or_repo]
    if "/" in alias_or_repo:
        return alias_or_repo.split("/")[-1].lower(), alias_or_repo
    known = "、".join(MODELS)
    raise KeyError(f"未知模型别名: {alias_or_repo}（可用: {known}，或直接传 HuggingFace repo id）")


def models_dir() -> Path:
    directory = paths.DATA_ROOT / "models"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def local_path(repo: str) -> Path:
    """repo id → 本地模型目录（不保证存在）。"""
    return models_dir() / repo.split("/")[-1]


def local_path_if_exists(repo: str) -> Path | None:
    """本地目录存在且是完整模型时返回路径（有 config + 权重分片且无未完成文件）。"""
    candidate = local_path(repo)
    if not (candidate / "config.json").is_file():
        return None
    if list(candidate.glob("*.incomplete")):
        return None
    shards = list(candidate.glob("model-*.safetensors"))
    single = candidate / "model.safetensors"
    if not shards and not single.is_file():
        return None
    return candidate


def source(alias_or_repo: str) -> str:
    """模型加载统一入口：优先本地目录，否则返回 repo id。"""
    _, repo = resolve(alias_or_repo)
    local = local_path_if_exists(repo)
    return str(local) if local else repo
