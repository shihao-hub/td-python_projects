"""OpenCode 配置读取：模型写死的思考档位合并（FR-4 / AC-5）。

与 opencode 的加载顺序一致（config.json → opencode.json → opencode.jsonc），
后加载的覆盖先加载的；配置缺失或解析失败（如 jsonc 带注释）不影响主流程，
跳过即可——与归档 ocstat 行为一致（jsonc 不实现注释剥离器，记为已知近似）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# providerID → modelID → 写死在模型 options 里的档位。
# 这类模型（variant 恒为 default/缺失）的真实档位需从配置合并。
EffortConfig = dict[str, dict[str, str]]

_CONFIG_NAMES = ("config.json", "opencode.json", "opencode.jsonc")


def _config_dir() -> Path:
    override = os.environ.get("OPENCODE_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "opencode"


def load_effort_config() -> tuple[EffortConfig, bool]:
    """读取三份配置并合并；返回 (config, 是否合并到有效档位)。"""
    cfg: EffortConfig = {}
    root = _config_dir()
    for name in _CONFIG_NAMES:
        try:
            data = (root / name).read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            doc = json.loads(data)
        except json.JSONDecodeError:
            continue  # jsonc 注释等：跳过，不中断主统计
        if not isinstance(doc, dict):
            continue
        providers = doc.get("provider")
        if not isinstance(providers, dict):
            continue
        for pid, pdata in providers.items():
            models = pdata.get("models") if isinstance(pdata, dict) else None
            if not isinstance(models, dict):
                continue
            for mid, mdata in models.items():
                if not isinstance(mdata, dict):
                    continue
                options = mdata.get("options")
                if not isinstance(options, dict):
                    continue
                eff = options.get("reasoningEffort") or options.get("effort") or ""
                if not eff:
                    continue
                cfg.setdefault(pid, {})[mid] = eff
    return cfg, len(cfg) > 0


def lookup_effort(cfg: EffortConfig, provider: str | None, model: str | None) -> str:
    if provider and model:
        return cfg.get(provider, {}).get(model, "")
    return ""
