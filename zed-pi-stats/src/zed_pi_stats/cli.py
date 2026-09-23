"""Command line interface for zed-pi-stats."""

from __future__ import annotations

import argparse
import json
import sys

from zed_pi_stats.collector import collect_all_sessions
from zed_pi_stats.reporter import (
    get_console,
    get_projected_schema,
    render_projected_json,
    render_tables,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="zed-pi-stats",
        description="Zed 编辑器 pi-acp 会话 Token 消耗与成本统计工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例用法:
  # 查看默认统计看板 (总览 + 最近10条会话 + 模型汇总 + 工作区汇总)
  zed-pi-stats

  # 仅查看模型消耗汇总 (终端表格)
  zed-pi-stats --by-model

  # 仅导出模型维度的结构化 JSON
  zed-pi-stats --by-model --json

  # 获取模型维度 JSON 的契约说明书 (JSON Schema)
  zed-pi-stats --by-model --schema

  # 展开完整明细列并显示大整数
  zed-pi-stats --wide --raw
""",
    )

    parser.add_argument(
        "-n", "--limit",
        type=int,
        default=10,
        help="最近会话明细展示条数 (默认: 10, 设为 0 展示全部)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="仅统计最近 N 天内的会话记录",
    )
    parser.add_argument(
        "--by-model",
        action="store_true",
        help="投影至模型维度：仅输出模型消耗汇总及对应的 JSON/Schema",
    )
    parser.add_argument(
        "--by-project",
        action="store_true",
        help="投影至工作区维度：仅输出工作区工程消耗汇总及对应的 JSON/Schema",
    )
    parser.add_argument(
        "--by-session",
        action="store_true",
        help="投影至会话明细维度：仅输出最近会话列表及对应的 JSON/Schema",
    )
    parser.add_argument(
        "-w", "--wide",
        action="store_true",
        help="强制展开宽表格 (包含输入/输出、缓存读取等明细列)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="显示原始带千分符的大整数 (不使用 K/M 缩写)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出结构化 JSON 格式 (与所选投影维度 --by-xxx 完全对齐)",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="输出当前命令或所选投影维度的 JSON Schema 契约 (零 I/O 极速反射)",
    )

    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    # 1. Zero-IO Schema Reflection (Pure metadata, no database/file I/O)
    if args.schema:
        schema = get_projected_schema(
            by_model=args.by_model,
            by_project=args.by_project,
            by_session=args.by_session,
        )
        print(json.dumps(schema, ensure_ascii=False, indent=2))
        sys.exit(0)

    console = get_console()

    # 2. Collect session data
    try:
        sessions = collect_all_sessions(days=args.days)
    except Exception as e:
        if args.json:
            error_payload = {
                "ok": False,
                "error": {
                    "code": "collect_failed",
                    "message": str(e),
                },
            }
            print(json.dumps(error_payload, ensure_ascii=False, indent=2))
        else:
            console.print(f"[bold red]数据采集失败:[/bold red] {e}")
        sys.exit(1)

    # 3. Output Projected JSON (Contract-aligned)
    if args.json:
        json_output = render_projected_json(
            sessions,
            by_model=args.by_model,
            by_project=args.by_project,
            by_session=args.by_session,
            limit=args.limit,
        )
        print(json_output)
        sys.exit(0)

    # 4. Human-readable Terminal Views
    has_filter = args.by_model or args.by_project or args.by_session
    show_sessions = args.by_session or not has_filter
    show_models = args.by_model or not has_filter
    show_projects = args.by_project or not has_filter

    render_tables(
        sessions,
        show_sessions=show_sessions,
        show_models=show_models,
        show_projects=show_projects,
        limit=args.limit,
        wide=args.wide,
        raw_numbers=args.raw,
        console=console,
    )


if __name__ == "__main__":
    main()
