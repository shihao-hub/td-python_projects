"""会话时间线 → Markdown（喂给其他 AI 的自包含交接文件）。

文件结构固定为三段：
1. YAML front-matter：机器可读的会话身份与统计；
2. 交接摘要卡片：接手者先读这一段就能知道「做了什么、改了哪些文件、下一步」；
3. 对话全文：按事件顺序还原的用户/助手/工具/系统摘要。

详略由 ``detail`` 控制：
- ``minimal``：只保留用户、助手、系统摘要；
- ``brief``（默认）：增加工具调用参数摘要与截断后的结果；
- ``full``：工具结果全量（可能极大）。
"""

from __future__ import annotations

import json

from ..core.handoff import Handoff, render_card
from ..core.trajectory import TimelineEvent

# 各详略档位下单条工具结果的字符上限
_RESULT_LIMIT = {"minimal": 0, "brief": 300, "full": 4000}
# 工具调用参数上限
_ARG_LIMIT = {"minimal": 0, "brief": 600, "full": 4000}

_ROLE_TITLES = {
    "user": "👤 用户",
    "assistant": "🤖 助手",
    "thinking": "💭 思考",
    "system": "📋 系统摘要",
    "error": "⚠️ 错误",
}


def _yaml_value(value: object) -> str:
    """YAML 标量：字符串统一加引号并转义，避免冒号/中文标点破坏解析。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _fmt_ts(ts: str) -> str:
    """ISO 时间 → ``YYYY-MM-DD HH:MM``；无时区信息时原样截断。"""
    if not ts:
        return ""
    text = ts.replace("T", " ")
    return text[:16]


def _pretty_args(text: str, limit: int) -> str:
    """工具参数：能解析成 JSON 就美化输出，否则原样截断。"""
    if limit <= 0:
        return ""
    try:
        obj = json.loads(text)
        body = json.dumps(obj, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, ValueError):
        body = text
    body = body.strip()
    if len(body) > limit:
        body = body[:limit] + "\n…[参数已截断]"
    return body


def render_front_matter(handoff: Handoff) -> str:
    """会话元信息 front-matter（AI 解析用，字段名稳定）。"""
    tool_total = sum(handoff.tool_counts.values())
    fields: list[tuple[str, object]] = [
        ("agent", handoff.agent),
        ("session_id", handoff.session_id),
        ("zed_thread_id", handoff.thread_id),
        ("title", handoff.title),
        ("directory", handoff.directory),
        ("created_at", handoff.created_at),
        ("updated_at", handoff.updated_at),
        ("events", handoff.event_count),
        ("user_turns", len(handoff.user_requests)),
        ("tool_calls", tool_total),
        ("errors", handoff.error_count),
        ("reconstructed", handoff.reconstructed),
    ]
    lines = ["---"]
    lines.extend(f"{key}: {_yaml_value(value)}" for key, value in fields if value != "")
    lines.append("---")
    return "\n".join(lines)


def render_markdown(handoff: Handoff, events: list[TimelineEvent], *,
                    detail: str = "brief") -> str:
    """完整 Markdown：front-matter + 交接卡片 + 对话全文。"""
    if detail not in _RESULT_LIMIT:
        detail = "brief"
    result_limit = _RESULT_LIMIT[detail]
    arg_limit = _ARG_LIMIT[detail]

    lines: list[str] = [render_front_matter(handoff), ""]
    lines.append(f"# {handoff.title or '(未命名会话)'}")
    lines.append("")
    lines.append(render_card(handoff))
    lines.append("---")
    lines.append("")
    lines.append("# 对话全文")
    lines.append("")

    shown = 0
    for event in events:
        if event.role == "tool_result":
            if result_limit <= 0:
                continue
            body = event.text.strip()
            if len(body) > result_limit:
                body = body[:result_limit] + "\n…[结果已截断]"
            lines.append(f"### 📤 工具结果 · `{event.name or 'tool'}`")
            if event.ts:
                lines.append(f"*{_fmt_ts(event.ts)}*")
            lines.append("")
            lines.append("```text")
            lines.append(body)
            lines.append("```")
            lines.append("")
            shown += 1
            continue
        if event.role == "tool_call":
            if arg_limit <= 0:
                continue
            args = _pretty_args(event.text, arg_limit)
            lines.append(f"### 🔧 工具调用 · `{event.name or 'tool'}`")
            if event.ts:
                lines.append(f"*{_fmt_ts(event.ts)}*")
            lines.append("")
            if args:
                lines.append("```json")
                lines.append(args)
                lines.append("```")
            lines.append("")
            shown += 1
            continue
        if event.role == "raw":
            if detail != "full":
                continue
            lines.append("### ❔ 原始事件")
            lines.append("")
            lines.append("```json")
            lines.append(event.text)
            lines.append("```")
            lines.append("")
            shown += 1
            continue
        title = _ROLE_TITLES.get(event.role, f"❔ {event.role}")
        lines.append(f"## {title}")
        if event.ts:
            lines.append(f"*{_fmt_ts(event.ts)}*")
        lines.append("")
        lines.append(event.text.rstrip())
        lines.append("")
        shown += 1

    if shown == 0:
        lines.append("*(无可展示事件)*")
        lines.append("")
    return "\n".join(lines)


def write_markdown(handoff: Handoff, events: list[TimelineEvent], output: str,
                   *, detail: str = "brief") -> int:
    """写入 Markdown 文件，返回字节数（UTF-8 无 BOM）。"""
    text = render_markdown(handoff, events, detail=detail)
    data = text.encode("utf-8")
    with open(output, "wb") as fh:
        fh.write(data)
    return len(data)


__all__ = ["render_front_matter", "render_markdown", "write_markdown"]
