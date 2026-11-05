"""会话交接摘要：从 TimelineEvent 流提炼「接手 AI 先读这一段就够」的上下文。

不做语义总结（不调模型），只做**确定性的结构提取**：
- 用户请求时间线（首条即会话意图）；
- 工具调用足迹：改过/读过的文件、执行过的命令（参数 JSON 里提取）；
- 工具调用次数分布与错误计数；
- agy 自带的 compaction CHECKPOINT（最贴近「进度摘要」的现成材料）。

输出同时服务两个读者：Markdown 卡片给人扫读，front-matter 化的字段给 AI
解析。所有截断都显式标注，不伪装完整。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .trajectory import TimelineEvent

# 提取上限（防止超大会话把摘要撑爆）
_MAX_USER_REQUESTS = 30
_MAX_FILES = 60
_MAX_COMMANDS = 40
_MAX_LAST_ASSISTANT = 1500
_LAST_ASSISTANT_TARGET = 600
_MAX_TITLE = 120
_MAX_PROGRESS = 6000
_MAX_COMMAND_CHARS = 220

# 工具参数里可能的文件路径键（agy 各工具命名不统一，按优先级依次尝试）
_FILE_KEYS = ("AbsolutePath", "absolute_path", "target_file", "TargetFile", "FilePath", "file_path")
_COMMAND_KEYS = ("CommandLine", "command", "Command")
_CWD_KEYS = ("Cwd", "cwd", "WorkingDirectory")

_JSON_FIELD_RE = re.compile(r'"([A-Za-z_]+)"\s*:\s*"((?:[^"\\]|\\.)*)"')


@dataclass
class Handoff:
    """会话交接摘要（字段即 Markdown 卡片与 front-matter 的共同数据源）。"""

    title: str = ""
    directory: str = ""
    agent: str = ""
    session_id: str = ""
    thread_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    reconstructed: bool = False
    user_requests: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    tool_counts: dict[str, int] = field(default_factory=dict)
    error_count: int = 0
    last_assistant: str = ""
    progress_summary: str = ""
    event_count: int = 0

    def to_dict(self) -> dict:
        return {
            "title": self.title, "directory": self.directory, "agent": self.agent,
            "session_id": self.session_id, "thread_id": self.thread_id,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "reconstructed": self.reconstructed,
            "user_requests": self.user_requests, "files": self.files,
            "commands": self.commands, "tool_counts": self.tool_counts,
            "error_count": self.error_count, "last_assistant": self.last_assistant,
            "progress_summary": self.progress_summary, "event_count": self.event_count,
        }


def _unescape(raw: str) -> str:
    """JSON 字符串转义还原（``\\\\`` → ``\\``）；失败时原样返回。"""
    try:
        return json.loads(f'"{raw}"')
    except (json.JSONDecodeError, ValueError):
        return raw


def _tool_args(text: str) -> dict[str, str]:
    """工具调用参数文本 → 扁平字段表（完整 JSON 优先，截断时正则兜底）。"""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return {str(k): v for k, v in obj.items() if isinstance(v, str)}
    except (json.JSONDecodeError, ValueError):
        pass
    return {m.group(1): _unescape(m.group(2)) for m in _JSON_FIELD_RE.finditer(text)}


def _norm_path(raw: str) -> str:
    """路径归一化：去多余引号/空白，统一反斜杠（展示用，不改大小写）。"""
    text = raw.strip().strip('"').replace("\\\\", "\\")
    return text.rstrip("\\")


def _flatten_command(raw: str) -> str:
    """命令压平成单行：去掉 JSON 外层引号与转义，供摘要列表展示。"""
    text = raw.strip().strip('"').replace('\\"', '"')
    text = " ".join(text.split())
    return text[:_MAX_COMMAND_CHARS] + ("…" if len(text) > _MAX_COMMAND_CHARS else "")


def _add_unique(bucket: list[str], value: str, limit: int) -> None:
    if not value or value in bucket or len(bucket) >= limit:
        return
    bucket.append(value)


def _looks_like_path(value: str) -> bool:
    """过滤明显不是文件路径的参数（URL、纯标识符）。"""
    if not value or value.startswith(("http://", "https://")):
        return False
    return ("/" in value or "\\" in value) and len(value) > 2


def build_handoff(events: list[TimelineEvent], *, meta: dict,
                  reconstructed: bool = False) -> Handoff:
    """事件流 + 会话元信息 → 交接摘要。"""
    handoff = Handoff(
        title=str(meta.get("title") or ""),
        directory=str(meta.get("directory") or ""),
        agent=str(meta.get("agent") or ""),
        session_id=str(meta.get("session_id") or ""),
        thread_id=str(meta.get("thread_id") or ""),
        created_at=str(meta.get("created_at") or ""),
        updated_at=str(meta.get("updated_at") or ""),
        reconstructed=reconstructed,
        event_count=len(events),
    )
    counts: Counter[str] = Counter()
    assistant_texts: list[str] = []
    for event in events:
        if event.role == "user" and event.text.strip():
            _add_unique(handoff.user_requests, event.text.strip(), _MAX_USER_REQUESTS)
        elif event.role == "assistant":
            if event.text.strip():
                assistant_texts.append(event.text.strip())
        elif event.role == "tool_call":
            counts[event.name or "tool"] += 1
            args = _tool_args(event.text)
            for key in _FILE_KEYS:
                value = args.get(key)
                if value:
                    value = _norm_path(value)
                    if _looks_like_path(value):
                        _add_unique(handoff.files, value, _MAX_FILES)
                    break
            for key in _COMMAND_KEYS:
                value = args.get(key)
                if value:
                    _add_unique(handoff.commands, _flatten_command(value), _MAX_COMMANDS)
                    break
        elif event.role == "error":
            handoff.error_count += 1
        elif event.role == "system" and event.text.strip():
            handoff.progress_summary = event.text.strip()[:_MAX_PROGRESS]

    # 目录兜底：会话元信息缺失时用工具调用的 Cwd / 文件父目录
    if not handoff.directory:
        for event in events:
            if event.role != "tool_call":
                continue
            args = _tool_args(event.text)
            for key in _CWD_KEYS:
                value = args.get(key)
                if value and Path(_norm_path(value)).is_dir():
                    handoff.directory = _norm_path(value)
                    break
            if handoff.directory:
                break

    # 标题兜底：首条用户请求压平
    if not handoff.title and handoff.user_requests:
        handoff.title = " ".join(handoff.user_requests[0].split())[:80]
    handoff.title = " ".join(handoff.title.split())[:_MAX_TITLE]

    # 结论：从尾部聚合最近几条助手正文（单条短句往往只是过渡语）
    chosen: list[str] = []
    total = 0
    for text in reversed(assistant_texts[-3:]):
        chosen.append(text)
        total += len(text)
        if total >= _LAST_ASSISTANT_TARGET:
            break
    if chosen:
        handoff.last_assistant = "\n\n".join(reversed(chosen))[:_MAX_LAST_ASSISTANT]
    handoff.tool_counts = dict(counts.most_common())
    return handoff


def _bullets(items: list[str], *, empty: str = "（无）", indent: str = "") -> list[str]:
    if not items:
        return [f"{indent}{empty}"]
    return [f"{indent}- `{item}`" for item in items]


def render_card(handoff: Handoff) -> str:
    """交接摘要 → Markdown 卡片（放在正文最前面，供人和 AI 快速接管）。"""
    lines: list[str] = ["## 给接手者的快速上下文", ""]
    if handoff.reconstructed:
        lines.append("> ⚠️ **启发式重建**：该会话没有明文 transcript，正文由 steps.db 的 "
                     "protobuf 文本块扫描还原，可能缺少工具参数与部分片段。")
        lines.append("")

    first_request = handoff.user_requests[0] if handoff.user_requests else "（未提取到）"
    first_request = " ".join(first_request.split())
    lines.append(f"- **任务**：{first_request}")
    if handoff.directory:
        lines.append(f"- **工作目录**：`{handoff.directory}`")
    if handoff.created_at or handoff.updated_at:
        lines.append(f"- **时间**：{handoff.created_at or '?'} → {handoff.updated_at or '?'}")
    lines.append(f"- **规模**：{handoff.event_count} 个事件 / {len(handoff.user_requests)} 轮用户输入"
                 f" / {sum(handoff.tool_counts.values())} 次工具调用")
    if handoff.error_count:
        lines.append(f"- **错误**：{handoff.error_count} 次（正文中标注为 error）")
    if handoff.tool_counts:
        top = "、".join(f"{k}×{v}" for k, v in list(handoff.tool_counts.items())[:8])
        lines.append(f"- **工具分布**：{top}")
    lines.append("")
    lines.append("**涉及文件**：")
    lines.extend(_bullets(handoff.files[:15]))
    if len(handoff.files) > 15:
        lines.append(f"  - …… 共 {len(handoff.files)} 个")
    lines.append("")
    lines.append("**执行命令**：")
    lines.extend(_bullets(handoff.commands[:10]))
    if len(handoff.commands) > 10:
        lines.append(f"  - …… 共 {len(handoff.commands)} 条")
    lines.append("")
    if handoff.last_assistant:
        lines.append("**最后一条助手结论**：")
        lines.append("")
        lines.append("> " + handoff.last_assistant.replace("\n", "\n> "))
        lines.append("")
    if handoff.progress_summary:
        lines.append("**agy 进度摘要（CHECKPOINT 原文）**：")
        lines.append("")
        lines.append("```text")
        lines.append(handoff.progress_summary)
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


__all__ = ["Handoff", "build_handoff", "render_card"]
