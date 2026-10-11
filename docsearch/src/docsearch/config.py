import os
import sys
from pathlib import Path
from typing import List
from pydantic import BaseModel, Field

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w


def get_default_data_dir() -> Path:
    """遵循父仓规范：数据文件存放在 %APPDATA%/language_projects/docsearch/"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        data_dir = Path(appdata) / "language_projects" / "docsearch"
    else:
        data_dir = Path.home() / ".language_projects" / "docsearch"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


class StorageConfig(BaseModel):
    db_path: str = Field(default="")

    def resolve_db_path(self, base_dir: Path) -> Path:
        if self.db_path:
            p = Path(self.db_path)
            if not p.is_absolute():
                p = base_dir / p
        else:
            p = base_dir / "lancedb"
        p.mkdir(parents=True, exist_ok=True)
        return p


class IndexingConfig(BaseModel):
    watch_dirs: List[str] = Field(default_factory=list)
    file_extensions: List[str] = Field(default_factory=lambda: [".md", ".markdown"])
    exclude_patterns: List[str] = Field(
        default_factory=lambda: [
            ".git",
            ".venv",
            "node_modules",
            "__pycache__",
            ".archived",
            "dist",
            "build",
        ]
    )
    chunk_max_characters: int = Field(default=800)
    chunk_overlap: int = Field(default=100)


class ModelConfig(BaseModel):
    embedding_model: str = Field(default="BAAI/bge-small-zh-v1.5")


class ServerConfig(BaseModel):
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8765)


class AppConfig(BaseModel):
    storage: StorageConfig = Field(default_factory=StorageConfig)
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


def get_default_config_path() -> Path:
    return get_default_data_dir() / "config.toml"


def load_config(config_path: Path | None = None) -> AppConfig:
    cfg_file = config_path or get_default_config_path()
    base_data_dir = get_default_data_dir()

    if not cfg_file.exists():
        # 默认收集常见的文档与 skill 目录
        candidate_dirs = [
            str(Path(r"D:\Users\language_projects\docs")),
            str(Path(r"D:\Users\language_projects\.agents\skills")),
            str(Path(r"C:\Users\29580\.gemini\config\skills")),
            str(Path(r"C:\Users\29580\.gemini\antigravity-cli\skills")),
        ]
        valid_dirs = [d for d in candidate_dirs if Path(d).exists()]
        if not valid_dirs:
            valid_dirs = [str(Path.cwd())]

        default_cfg = AppConfig(
            storage=StorageConfig(db_path=str(base_data_dir / "lancedb")),
            indexing=IndexingConfig(watch_dirs=valid_dirs),
            model=ModelConfig(embedding_model="BAAI/bge-small-zh-v1.5"),
            server=ServerConfig(host="127.0.0.1", port=8765),
        )
        save_config(default_cfg, cfg_file)
        return default_cfg

    try:
        with open(cfg_file, "rb") as f:
            data = tomllib.load(f)
        cfg = AppConfig.model_validate(data)
    except Exception as e:
        print(f"[docsearch] 加载配置文件失败 ({e})，使用默认配置", file=sys.stderr)
        cfg = AppConfig()

    return cfg


def save_config(cfg: AppConfig, config_path: Path | None = None) -> None:
    cfg_file = config_path or get_default_config_path()
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    dumped = cfg.model_dump()
    with open(cfg_file, "wb") as f:
        tomli_w.dump(dumped, f)
