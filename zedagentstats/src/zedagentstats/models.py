"""Data models and schemas for zedagentstats."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TokenUsage:
    """Token usage and estimated cost metrics."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0

    def add(self, other: TokenUsage) -> None:
        """Accumulate usage from another TokenUsage instance."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.total_tokens += other.total_tokens
        self.cost = round(self.cost + other.cost, 6)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModelUsage:
    """Token metrics attributed to a specific model."""

    model_key: str = ""
    provider: str = ""
    model: str = ""
    turns: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_key": self.model_key,
            "provider": self.provider,
            "model": self.model,
            "turns": self.turns,
            "usage": self.usage.to_dict(),
        }


@dataclass
class SessionStats:
    """Statistics for a single Zed ACP agent conversation session."""

    session_id: str
    agent: str = ""
    title: str | None = None
    folder_paths: list[str] = field(default_factory=list)
    cwd: str | None = None
    session_file: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    turns: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    total_usage: TokenUsage = field(default_factory=TokenUsage)
    model_usages: dict[str, ModelUsage] = field(default_factory=dict)

    @property
    def short_id(self) -> str:
        """First 8 chars of session_id like Git commit short SHA."""
        return self.session_id[:8] if self.session_id else "unknown"

    @property
    def title_display(self) -> str:
        """Title display: user title if present, otherwise [short_id]."""
        clean = (self.title or "").strip()
        if clean:
            return clean
        return f"[{self.short_id}]"

    @property
    def project_display(self) -> str:
        """Display name for project/workspace."""
        if self.folder_paths:
            return os.path.basename(os.path.normpath(self.folder_paths[0]))
        if self.cwd:
            return os.path.basename(os.path.normpath(self.cwd))
        return "zeddefault"

    @property
    def project_path(self) -> str | None:
        """Full path for project/workspace."""
        if self.folder_paths:
            return self.folder_paths[0]
        return self.cwd

    @property
    def model_display(self) -> str:
        """Summary string of models used in this session."""
        if not self.model_usages:
            return "unknown"
        models = list(self.model_usages.keys())
        if len(models) == 1:
            clean = models[0].split("/")[-1]
            if clean.endswith("-flash") and len(clean) > 16:
                clean = clean[:-6]
            return clean
        clean_first = models[0].split("/")[-1]
        return f"{clean_first} (+{len(models)-1})"

    def to_dict(self) -> dict[str, Any]:
        """Convert session to exportable dictionary."""
        return {
            "session_id": self.session_id,
            "agent": self.agent,
            "short_id": self.short_id,
            "title": self.title,
            "title_display": self.title_display,
            "project_name": self.project_display,
            "project_path": self.project_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turns": self.turns,
            "model_display": self.model_display,
            "total_usage": self.total_usage.to_dict(),
            "models": {k: v.to_dict() for k, v in self.model_usages.items()},
        }


# ==============================================================================
# Standard JSON Schema Definitions (Pure Python stdlib, Zero Dependencies)
# ==============================================================================

SUMMARY_SCHEMA = {
    "type": "object",
    "description": "全局概览汇总指标",
    "required": [
        "session_count", "turns", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
        "total_tokens", "cost"
    ],
    "properties": {
        "session_count": {"type": "integer", "description": "统计覆盖的会话总数"},
        "turns": {"type": "integer", "description": "交互总轮次"},
        "input_tokens": {"type": "integer", "description": "输入 tokens 数量"},
        "output_tokens": {"type": "integer", "description": "输出 tokens 数量"},
        "cache_read_tokens": {"type": "integer", "description": "缓存读取 tokens 数量"},
        "cache_write_tokens": {"type": "integer", "description": "缓存写入 tokens 数量"},
        "reasoning_tokens": {"type": "integer", "description": "思维链推理 tokens 数量"},
        "total_tokens": {"type": "integer", "description": "计费总 tokens 数量"},
        "cost": {"type": "number", "description": "预估总费用金额 (USD)"},
    },
}

MODEL_ITEM_SCHEMA = {
    "type": "object",
    "required": ["model_key", "session_count", "turns", "total_tokens", "cost"],
    "properties": {
        "model_key": {"type": "string", "description": "模型完整标识 (provider/model)"},
        "provider": {"type": "string", "description": "供应商名称"},
        "model": {"type": "string", "description": "模型名称"},
        "session_count": {"type": "integer", "description": "使用该模型的会话数"},
        "turns": {"type": "integer", "description": "调用轮次"},
        "input_tokens": {"type": "integer", "description": "输入 tokens"},
        "output_tokens": {"type": "integer", "description": "输出 tokens"},
        "cache_read_tokens": {"type": "integer", "description": "缓存读取 tokens"},
        "cache_write_tokens": {"type": "integer", "description": "缓存写入 tokens"},
        "reasoning_tokens": {"type": "integer", "description": "推理 tokens"},
        "total_tokens": {"type": "integer", "description": "计费总 tokens"},
        "cost": {"type": "number", "description": "预估总费用 (USD)"},
    },
}

PROJECT_ITEM_SCHEMA = {
    "type": "object",
    "required": ["project", "session_count", "turns", "total_tokens", "cost"],
    "properties": {
        "project": {"type": "string", "description": "工作区或项目名称"},
        "session_count": {"type": "integer", "description": "会话总数"},
        "turns": {"type": "integer", "description": "交互轮次"},
        "input_tokens": {"type": "integer", "description": "输入 tokens"},
        "output_tokens": {"type": "integer", "description": "输出 tokens"},
        "cache_read_tokens": {"type": "integer", "description": "缓存读取 tokens"},
        "cache_write_tokens": {"type": "integer", "description": "缓存写入 tokens"},
        "reasoning_tokens": {"type": "integer", "description": "推理 tokens"},
        "total_tokens": {"type": "integer", "description": "消耗总 tokens"},
        "cost": {"type": "number", "description": "预估总费用 (USD)"},
    },
}

SESSION_ITEM_SCHEMA = {
    "type": "object",
    "required": ["session_id", "short_id", "title_display", "project_name", "turns", "total_usage"],
    "properties": {
        "session_id": {"type": "string", "description": "会话 UUID 或会话 ID"},
        "agent": {"type": "string", "description": "所属智能体 (pi / antigravity / opencode)"},
        "short_id": {"type": "string", "description": "8位短会话 ID"},
        "title": {"type": ["string", "null"], "description": "用户自定义会话标题"},
        "title_display": {"type": "string", "description": "展示标题 (缺省时展示短 ID)"},
        "project_name": {"type": "string", "description": "工作区/工程名称"},
        "project_path": {"type": ["string", "null"], "description": "本地工作区绝对路径"},
        "created_at": {"type": ["string", "null"], "description": "会话创建时间 (ISO 8601)"},
        "updated_at": {"type": ["string", "null"], "description": "最后更新时间 (ISO 8601)"},
        "turns": {"type": "integer", "description": "交互轮次"},
        "model_display": {"type": "string", "description": "所用模型概括"},
        "total_usage": {"type": "object", "description": "会话累积消耗指标"},
        "models": {"type": "object", "description": "按模型分别计量的消耗明细"},
    },
}

AGENT_ITEM_SCHEMA = {
    "type": "object",
    "required": ["agent", "session_count", "turns", "total_tokens", "cost"],
    "properties": {
        "agent": {"type": "string", "description": "智能体标识 (pi / antigravity / opencode)"},
        "display_name": {"type": "string", "description": "智能体人读名称"},
        "session_count": {"type": "integer", "description": "会话总数"},
        "turns": {"type": "integer", "description": "交互轮次"},
        "input_tokens": {"type": "integer", "description": "输入 tokens"},
        "output_tokens": {"type": "integer", "description": "输出 tokens"},
        "cache_read_tokens": {"type": "integer", "description": "缓存读取 tokens"},
        "cache_write_tokens": {"type": "integer", "description": "缓存写入 tokens"},
        "reasoning_tokens": {"type": "integer", "description": "推理 tokens"},
        "total_tokens": {"type": "integer", "description": "计费总 tokens"},
        "cost": {"type": "number", "description": "预估总费用 (USD)"},
    },
}


def get_full_schema() -> dict[str, Any]:
    """Return JSON Schema for the full multi-dimensional report."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ZedagentstatsFullReport",
        "description": "Zed ACP 智能体会话 Token 消耗全量多维统计报告契约",
        "type": "object",
        "required": ["summary", "by_agent", "by_model", "by_project", "sessions"],
        "properties": {
            "summary": SUMMARY_SCHEMA,
            "by_agent": {"type": "array", "items": AGENT_ITEM_SCHEMA},
            "by_model": {"type": "array", "items": MODEL_ITEM_SCHEMA},
            "by_project": {"type": "array", "items": PROJECT_ITEM_SCHEMA},
            "sessions": {"type": "array", "items": SESSION_ITEM_SCHEMA},
        },
    }


def get_model_schema() -> dict[str, Any]:
    """Return JSON Schema for model projection."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ZedagentstatsModelReport",
        "description": "Zed ACP 智能体模型维度 Token 消耗统计契约",
        "type": "object",
        "required": ["summary", "by_model"],
        "properties": {
            "summary": SUMMARY_SCHEMA,
            "by_model": {"type": "array", "items": MODEL_ITEM_SCHEMA},
        },
    }


def get_project_schema() -> dict[str, Any]:
    """Return JSON Schema for project/workspace projection."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ZedagentstatsProjectReport",
        "description": "Zed ACP 智能体工作区工程维度 Token 消耗统计契约",
        "type": "object",
        "required": ["summary", "by_project"],
        "properties": {
            "summary": SUMMARY_SCHEMA,
            "by_project": {"type": "array", "items": PROJECT_ITEM_SCHEMA},
        },
    }


def get_session_schema() -> dict[str, Any]:
    """Return JSON Schema for session list projection."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ZedagentstatsSessionReport",
        "description": "Zed ACP 智能体会话明细维度 Token 消耗统计契约",
        "type": "object",
        "required": ["summary", "sessions"],
        "properties": {
            "summary": SUMMARY_SCHEMA,
            "sessions": {"type": "array", "items": SESSION_ITEM_SCHEMA},
        },
    }
