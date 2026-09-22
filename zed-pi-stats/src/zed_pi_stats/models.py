"""Data models for zed-pi-stats."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def add(self, other: TokenUsage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.cost += other.cost

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "cost": round(self.cost, 6),
        }


@dataclass
class ModelUsage:
    model_key: str  # e.g. "opencode-go/gpt-5.6-luna" or "deepseek-v4.1-flash"
    provider: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    turns: int = 0

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
    session_id: str
    title: str = ""
    folder_paths: list[str] = field(default_factory=list)
    cwd: str = ""
    session_file: str = ""
    created_at: str = ""
    updated_at: str = ""
    turns: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    total_usage: TokenUsage = field(default_factory=TokenUsage)
    model_usages: dict[str, ModelUsage] = field(default_factory=dict)

    @property
    def model_display(self) -> str:
        models = list(self.model_usages.keys())
        if not models:
            return "unknown"
        if len(models) == 1:
            return models[0].split("/")[-1]
        short_names = [m.split("/")[-1] for m in models]
        return f"multi ({', '.join(short_names)})"

    @property
    def project_display(self) -> str:
        if self.folder_paths:
            # First folder basename
            parts = [p.replace("\\", "/").rstrip("/").split("/")[-1] for p in self.folder_paths if p]
            return ", ".join(parts) if parts else "untitled"
        if self.cwd:
            return self.cwd.replace("\\", "/").rstrip("/").split("/")[-1]
        return "untitled"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "project": self.project_display,
            "folder_paths": self.folder_paths,
            "cwd": self.cwd,
            "session_file": self.session_file,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turns": self.turns,
            "user_messages": self.user_messages,
            "assistant_messages": self.assistant_messages,
            "model_display": self.model_display,
            "total_usage": self.total_usage.to_dict(),
            "models": {k: v.to_dict() for k, v in self.model_usages.items()},
        }
