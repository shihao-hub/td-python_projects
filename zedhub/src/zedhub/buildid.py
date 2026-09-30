"""构建身份：生产/开发构建判定与 buildID 生成（v2 握手凭据）。

生产/开发判定的语义对齐 v2《CLI 工具开发标准》3.2：
- 生产构建 = 包版本为干净发布号（HEAD 恰在 zedhub-v* tag 上构建）；
- 开发构建 = 版本含 `.dev` 段或 local `+` 段（tag 之后有新提交、无 tag、
  或源码 editable 运行）。

buildID：
- 生产 = `v{version}`；
- 开发 = `dev-{源码指纹}`——对包内全部 .py 的 相对路径+大小+mtime 做
  聚合哈希。Python 没有链接期注入，靠指纹让「改了代码只重启了一边」
  在握手时当场报错。
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path

FALLBACK_VERSION = "0.0.0.dev0"


@lru_cache(maxsize=1)
def package_version() -> str:
    """包版本（构建时由 hatch-vcs 从 git tag 推导写入）。"""
    try:
        return pkg_version("zedhub")
    except PackageNotFoundError:
        return FALLBACK_VERSION


@lru_cache(maxsize=1)
def is_production_build() -> bool:
    """干净发布号即生产构建；含 .dev/+(local) 段即开发构建。"""
    v = package_version()
    return ".dev" not in v and "+" not in v


@lru_cache(maxsize=1)
def source_fingerprint() -> str:
    """源码指纹（12 hex）：单边重启检测用，生产构建不参与 buildID。"""
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        st = p.stat()
        h.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:12]


@lru_cache(maxsize=1)
def build_id() -> str:
    """握手凭据：两侧相等放行，不等提示重启 daemon。"""
    if is_production_build():
        return f"v{package_version()}"
    return f"dev-{source_fingerprint()}"
