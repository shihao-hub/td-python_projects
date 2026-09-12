#!/usr/bin/env python
"""
将 get_session_content 返回的 JSON 转为 Markdown 文档。

用法:
    # 直接查询并导出
    uv run python scripts/export_markdown.py <session_id> -o output.md
    
    # 从 stdin 读取 JSON
    uv run zoc show ses_xxx --json | uv run python scripts/export_markdown.py --stdin -o output.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def format_timestamp(ts_ms: int) -> str:
    """毫秒时间戳转本地时间字符串"""
    dt = datetime.fromtimestamp(ts_ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def export_markdown(data: dict, output: Path) -> None:
    """将 session content JSON 转为 Markdown"""
    session = data["session"]
    messages = data["messages"]

    lines = []

    # 标题和元数据
    lines.append(f"# {session['title']}\n")
    lines.append(f"**Session ID**: `{session['id']}`  ")
    lines.append(f"**Directory**: `{session['directory']}`  ")
    lines.append(f"**Agent**: {session.get('agent', 'N/A')}  ")
    lines.append(
        f"**Model**: {json.dumps(session.get('model'), ensure_ascii=False) if session.get('model') else 'N/A'}  "
    )
    lines.append(f"**Created**: {format_timestamp(session['time_created'])}  ")
    lines.append(f"**OpenCode Version**: {session['version']}\n")
    lines.append("---\n")

    # 对话内容
    for msg in messages:
        role = msg["role"]
        emoji = "👤" if role == "user" else "🤖"
        lines.append(f"## {emoji} {role.capitalize()}\n")
        lines.append(
            f"*{format_timestamp(msg['time_created'])}* | Agent: {msg.get('agent', 'N/A')} | Model: {msg.get('modelID', 'N/A')}\n"
        )

        # 只展示 type=text 的 part
        text_parts = [p for p in msg["parts"] if p["type"] == "text" and p["text"].strip()]
        if text_parts:
            for part in text_parts:
                lines.append(part["text"])
                lines.append("\n")
        else:
            lines.append("*(no text content)*\n")

        lines.append("---\n")

    # 写文件
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Exported to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export session content to Markdown")
    parser.add_argument("session_id", nargs="?", help="Session ID to query (或 --stdin)")
    parser.add_argument("--stdin", action="store_true", help="从 stdin 读取 JSON")
    parser.add_argument("-o", "--output", required=True, help="输出 Markdown 文件路径")
    args = parser.parse_args()

    if args.stdin:
        # 从 stdin 读取 JSON（显式 UTF-8，容错 BOM）
        import io
        reader = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8-sig")
        raw = reader.read()
        envelope = json.loads(raw)
        # 如果是 CLI 的 JSON 信封格式，提取 data 字段
        if "data" in envelope and "status" in envelope:
            data = envelope["data"]
        else:
            data = envelope
    elif args.session_id:
        # 直接查询数据库
        # 需要导入项目模块
        try:
            from zoc.tools import get_session_content

            data = get_session_content(args.session_id)
        except ImportError:
            print("Error: Cannot import zoc module. Please run from project root with 'uv run'.", file=sys.stderr)
            sys.exit(1)
    else:
        parser.error("需要指定 session_id 或 --stdin")

    export_markdown(data, Path(args.output))


if __name__ == "__main__":
    main()
