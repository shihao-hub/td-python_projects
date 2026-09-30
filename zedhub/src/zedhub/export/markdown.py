"""Markdown 导出（移植归档 export_markdown.py 的可读结构）。"""

from __future__ import annotations

from pathlib import Path


def _fmt_ts(iso: str | None) -> str:
    return iso[:19].replace("T", " ") if iso else "-"


def render_markdown(content: dict) -> str:
    """sessions.content 载荷 → Markdown 文本。"""
    s = content
    model = s.get("model") or {}
    lines: list[str] = []
    lines.append(f"# {s['title'] or '(untitled)'}\n")
    lines.append(f"**Session ID**: `{s['external_id']}`  ")
    lines.append(f"**Directory**: `{s.get('directory') or '-'}`  ")
    lines.append(f"**Agent**: {s.get('agent') or 'N/A'}  ")
    if model:
        variant = f" ({model['variant']})" if model.get("variant") else ""
        lines.append(f"**Model**: {model.get('provider') or '-'}/{model.get('model_id') or '-'}{variant}  ")
    lines.append(f"**Created**: {_fmt_ts(s.get('created_at'))}  ")
    if s.get("zed_thread_id"):
        lines.append(f"**Zed thread**: `{s['zed_thread_id']}`  ")
    lines.append("---\n")

    for m in s.get("messages", []):
        role = m["role"]
        emoji = "👤" if role == "user" else "🤖"
        lines.append(f"## {emoji} {role.capitalize()}\n")
        lines.append(
            f"*{_fmt_ts(m.get('created_at'))}* | Agent: {m.get('agent') or 'N/A'} | "
            f"Model: {m.get('model_id') or 'N/A'}\n"
        )
        text_parts = [
            p for p in m.get("parts", [])
            if p.get("type") == "text" and (p.get("text") or "").strip()
        ]
        if text_parts:
            for p in text_parts:
                lines.append(p["text"].rstrip())
                lines.append("\n")
        else:
            lines.append("*(no text content)*\n")
        lines.append("---\n")
    return "\n".join(lines)


def write_markdown(content: dict, output: Path) -> None:
    output.write_text(render_markdown(content), encoding="utf-8")
