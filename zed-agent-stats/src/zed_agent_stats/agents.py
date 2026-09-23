"""Agent 注册表：定义 zed-agent-stats 支持的 ACP 智能体规格。

每个 AgentSpec 描述一个 Zed 内 ACP 智能体的身份标识与数据采集器，
Service 核心据此路由子命令到对应 collector。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable

from zed_agent_stats.models import SessionStats


@dataclass(frozen=True)
class AgentSpec:
    """一个可统计的 ACP 智能体规格。"""

    cli_name: str                # 子命令名
    zed_id: str                  # Zed sidebar_threads.agent_id
    display_name: str            # 人读名称
    aliases: tuple[str, ...] = ()  # 子命令别名
    collector_module: str = ""   # 采集器模块路径 (collectors/ 下)


# 注册顺序即总览展示顺序
_REGISTRY: list[AgentSpec] = [
    AgentSpec(
        cli_name="pi",
        zed_id="pi-acp",
        display_name="Zed pi-acp",
        aliases=(),
        collector_module="zed_agent_stats.collectors.pi",
    ),
    AgentSpec(
        cli_name="antigravity",
        zed_id="antigravity-acp",
        display_name="Zed antigravity-acp",
        aliases=("agy",),
        collector_module="zed_agent_stats.collectors.antigravity",
    ),
    AgentSpec(
        cli_name="opencode",
        zed_id="opencode",
        display_name="Zed opencode",
        aliases=("oc",),
        collector_module="zed_agent_stats.collectors.opencode",
    ),
]


def all_agents() -> list[AgentSpec]:
    """返回全部已注册 agent 规格（注册序）。"""
    return list(_REGISTRY)


def resolve(name_or_alias: str) -> AgentSpec | None:
    """按子命令名或别名解析 AgentSpec，未命中返回 None。"""
    for spec in _REGISTRY:
        if name_or_alias == spec.cli_name or name_or_alias in spec.aliases:
            return spec
    return None


def load_collector(spec: AgentSpec) -> Callable[..., list[SessionStats]]:
    """延迟加载 agent 对应的采集器 collect 函数。

    采集器模块必须暴露 ``collect(days: int | None) -> list[SessionStats]``。
    """
    module = importlib.import_module(spec.collector_module)
    return module.collect
