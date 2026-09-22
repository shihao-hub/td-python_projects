"""Command-line interface for zed-pi-stats."""

from __future__ import annotations

import argparse
import json
import sys

from zed_pi_stats import __version__
from zed_pi_stats.collector import collect_all_sessions
from zed_pi_stats.reporter import get_console, render_json, render_tables

JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ZedPiStatsReport",
    "type": "object",
    "properties": {
        "summary": {
            "type": "object",
            "properties": {
                "session_count": {"type": "integer"},
                "turns": {"type": "integer"},
                "input_tokens": {"type": "integer"},
                "output_tokens": {"type": "integer"},
                "cache_read_tokens": {"type": "integer"},
                "cache_write_tokens": {"type": "integer"},
                "reasoning_tokens": {"type": "integer"},
                "total_tokens": {"type": "integer"},
                "cost": {"type": "number"},
            },
            "required": ["session_count", "turns", "total_tokens", "cost"],
        },
        "by_model": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "model_key": {"type": "string"},
                    "provider": {"type": "string"},
                    "model": {"type": "string"},
                    "session_count": {"type": "integer"},
                    "turns": {"type": "integer"},
                    "total_tokens": {"type": "integer"},
                    "cost": {"type": "number"},
                },
            },
        },
        "by_project": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "session_count": {"type": "integer"},
                    "turns": {"type": "integer"},
                    "total_tokens": {"type": "integer"},
                    "cost": {"type": "number"},
                },
            },
        },
        "sessions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "title": {"type": "string"},
                    "project": {"type": "string"},
                    "updated_at": {"type": "string"},
                    "turns": {"type": "integer"},
                    "total_usage": {"type": "object"},
                    "models": {"type": "object"},
                },
            },
        },
    },
    "required": ["summary", "by_model", "by_project", "sessions"],
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zed-pi-stats",
        description="统计 Zed 编辑器中 pi-acp agent 会话消耗的 Token 与费用",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="显示版本信息并退出",
    )
    parser.add_argument(
        "-n", "--limit",
        type=int,
        default=10,
        help="最近会话明细展示条数（默认 10，设为 0 表示展示全部）",
    )
    parser.add_argument(
        "-d", "--days",
        type=int,
        default=None,
        help="仅统计最近 N 天内的会话记录",
    )
    parser.add_argument(
        "--by-model",
        action="store_true",
        help="仅输出模型消耗汇总",
    )
    parser.add_argument(
        "--by-project",
        action="store_true",
        help="仅输出工作区工程消耗汇总",
    )
    parser.add_argument(
        "--by-session",
        action="store_true",
        help="仅输出最近会话明细列表",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出统计结果（供脚本解析或集成）",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="导出 JSON 输出的 Schema 结构定义",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.schema:
        print(json.dumps(JSON_SCHEMA, ensure_ascii=False, indent=2))
        return 0

    sessions = collect_all_sessions(days=args.days)

    if args.json:
        # 仅限制明细列表，汇总仍覆盖筛选后的全部会话。
        out_sessions = sessions[:args.limit] if args.limit > 0 else sessions
        print(render_json(sessions, out_sessions))
        return 0

    console = get_console()
    limit = len(sessions) if args.limit <= 0 else args.limit

    # Determine which tables to display
    has_specific_view = args.by_model or args.by_project or args.by_session
    if has_specific_view:
        show_sessions = args.by_session
        show_models = args.by_model
        show_projects = args.by_project
    else:
        show_sessions = True
        show_models = True
        show_projects = True

    render_tables(
        sessions=sessions,
        show_sessions=show_sessions,
        show_models=show_models,
        show_projects=show_projects,
        limit=limit,
        console=console,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
