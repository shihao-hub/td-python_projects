"""Command line interface for zed-agent-stats."""

from __future__ import annotations

import argparse
import json
import sys

from zed_agent_stats.agents import all_agents, load_collector, resolve
from zed_agent_stats.reporter import (
    get_console,
    get_projected_schema,
    render_projected_json,
    render_tables,
)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """为顶层与所有子命令挂载共享投影/输出参数。"""
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


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="zed-agent-stats",
        description="Zed 编辑器 ACP 智能体（pi / antigravity / opencode）会话 Token 消耗与成本统计工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例用法 (参数请放在子命令之后):
  # 合并总览：全部 agent (总览 + 最近10条会话 + 模型汇总 + 工作区汇总)
  zed-agent-stats

  # pi 智能体默认看板
  zed-agent-stats pi

  # antigravity (缩写 agy) 模型消耗汇总
  zed-agent-stats antigravity --by-model
  zed-agent-stats agy --by-model

  # opencode (缩写 oc) 会话明细 JSON
  zed-agent-stats oc --by-session --json

  # 获取模型维度 JSON 的契约说明书 (JSON Schema)
  zed-agent-stats pi --by-model --schema

  # 展开完整明细列并显示大整数
  zed-agent-stats --wide --raw
""",
    )
    _add_common_args(parser)

    subparsers = parser.add_subparsers(
        dest="agent",
        metavar="{pi,antigravity,opencode}",
        help="选择智能体 (缺省: 合并总览)",
    )
    for spec in all_agents():
        sp = subparsers.add_parser(
            spec.cli_name,
            aliases=list(spec.aliases),
            help=f"{spec.display_name} 会话统计 (缩写: {', '.join(spec.aliases) or '无'})",
        )
        _add_common_args(sp)

    return parser


def _collect_sessions(agent_name: str | None, days: int | None):
    """按子命令收集单个 agent 或全部 agent 的会话数据。

    Returns:
        (sessions, unavailable) — 后者为采集失败/未实现的 agent 显示名列表。
    """
    specs = [resolve(agent_name)] if agent_name else all_agents()
    sessions = []
    unavailable: list[str] = []
    for spec in specs:
        try:
            collector = load_collector(spec)
            sessions.extend(collector(days=days))
        except NotImplementedError:
            unavailable.append(spec.display_name)
        except Exception:
            unavailable.append(spec.display_name)
    return sessions, unavailable


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    # 1. Zero-IO Schema Reflection (纯元数据反射，不触发任何数据库/文件 I/O)
    if args.schema:
        schema = get_projected_schema(
            by_model=args.by_model,
            by_project=args.by_project,
            by_session=args.by_session,
        )
        print(json.dumps(schema, ensure_ascii=False, indent=2))
        sys.exit(0)

    console = get_console()

    # 2. Collect session data (按子命令路由到对应 agent 采集器)
    try:
        sessions, unavailable = _collect_sessions(args.agent, days=args.days)
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

    if unavailable and not args.json:
        console.print(
            "[yellow]以下 agent 数据源暂不可用，已跳过:[/yellow] " + ", ".join(unavailable)
        )

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
        agent_name=args.agent,
    )


if __name__ == "__main__":
    main()
