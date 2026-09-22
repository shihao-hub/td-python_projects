"""Reporting and aggregation views for zed-pi-stats."""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.box import ROUNDED
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zed_pi_stats.models import SessionStats, TokenUsage


# Ensure stdout/stderr handles UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def format_num(n: int) -> str:
    """Format an integer with commas for readability."""
    return f"{n:,}"


def format_cost(cost: float) -> str:
    """Format cost nicely (e.g. $0.0012, $1.25)."""
    if cost == 0:
        return "$0.0000"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def format_time(ts: str) -> str:
    """Format ISO timestamp to shorter human-friendly time."""
    if not ts:
        return "-"
    try:
        clean = ts.replace("Z", "").split(".")[0]
        if "T" in clean:
            date_part, time_part = clean.split("T")
            m_d = "-".join(date_part.split("-")[1:])
            h_m = ":".join(time_part.split(":")[:2])
            return f"{m_d} {h_m}"
    except Exception:
        pass
    return ts[:16]


def aggregate_by_model(sessions: list[SessionStats]) -> list[dict[str, Any]]:
    """Aggregate token usage and cost grouped by model across all sessions."""
    model_map: dict[str, dict[str, Any]] = {}

    for s in sessions:
        for model_key, mu in s.model_usages.items():
            if model_key not in model_map:
                model_map[model_key] = {
                    "model_key": model_key,
                    "provider": mu.provider,
                    "model": mu.model,
                    "sessions": set(),
                    "turns": 0,
                    "usage": TokenUsage(),
                }
            entry = model_map[model_key]
            entry["sessions"].add(s.session_id)
            entry["turns"] += mu.turns
            entry["usage"].add(mu.usage)

    results = []
    for item in model_map.values():
        u: TokenUsage = item["usage"]
        results.append({
            "model_key": item["model_key"],
            "provider": item["provider"],
            "model": item["model"],
            "session_count": len(item["sessions"]),
            "turns": item["turns"],
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_tokens": u.cache_read_tokens,
            "cache_write_tokens": u.cache_write_tokens,
            "reasoning_tokens": u.reasoning_tokens,
            "total_tokens": u.total_tokens,
            "cost": u.cost,
        })

    results.sort(key=lambda x: (x["cost"], x["total_tokens"]), reverse=True)
    return results


def aggregate_by_project(sessions: list[SessionStats]) -> list[dict[str, Any]]:
    """Aggregate token usage and cost grouped by workspace project."""
    project_map: dict[str, dict[str, Any]] = {}

    for s in sessions:
        proj = s.project_display
        if proj not in project_map:
            project_map[proj] = {
                "project": proj,
                "session_count": 0,
                "turns": 0,
                "usage": TokenUsage(),
            }
        entry = project_map[proj]
        entry["session_count"] += 1
        entry["turns"] += s.turns
        entry["usage"].add(s.total_usage)

    results = []
    for item in project_map.values():
        u: TokenUsage = item["usage"]
        results.append({
            "project": item["project"],
            "session_count": item["session_count"],
            "turns": item["turns"],
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_tokens": u.cache_read_tokens,
            "cache_write_tokens": u.cache_write_tokens,
            "reasoning_tokens": u.reasoning_tokens,
            "total_tokens": u.total_tokens,
            "cost": u.cost,
        })

    results.sort(key=lambda x: (x["cost"], x["total_tokens"]), reverse=True)
    return results


def aggregate_grand_total(sessions: list[SessionStats]) -> dict[str, Any]:
    """Compute overall totals across all sessions."""
    total_u = TokenUsage()
    total_turns = 0
    for s in sessions:
        total_u.add(s.total_usage)
        total_turns += s.turns

    return {
        "session_count": len(sessions),
        "turns": total_turns,
        "input_tokens": total_u.input_tokens,
        "output_tokens": total_u.output_tokens,
        "cache_read_tokens": total_u.cache_read_tokens,
        "cache_write_tokens": total_u.cache_write_tokens,
        "reasoning_tokens": total_u.reasoning_tokens,
        "total_tokens": total_u.total_tokens,
        "cost": total_u.cost,
    }


def get_console() -> Console:
    """Create a Console with Windows UTF-8 safety."""
    return Console(legacy_windows=False)


def render_tables(
    sessions: list[SessionStats],
    show_sessions: bool = True,
    show_models: bool = True,
    show_projects: bool = True,
    limit: int = 10,
    console: Console | None = None,
) -> None:
    """Print beautifully formatted Rich tables to terminal."""
    if console is None:
        console = get_console()

    if not sessions:
        console.print("[yellow]未找到匹配的 Zed pi-acp 会话记录。[/yellow]")
        return

    grand = aggregate_grand_total(sessions)

    # 1. Grand Total Summary Panel
    summary_text = (
        f"[bold cyan]会话总数:[/bold cyan] {grand['session_count']}    "
        f"[bold cyan]总交互轮次:[/bold cyan] {grand['turns']}    "
        f"[bold green]总消耗 Tokens:[/bold green] {format_num(grand['total_tokens'])} "
        f"([dim]输入:[/dim] {format_num(grand['input_tokens'])}, "
        f"[dim]输出:[/dim] {format_num(grand['output_tokens'])}, "
        f"[dim]缓存读:[/dim] {format_num(grand['cache_read_tokens'])})    "
        f"[bold yellow]总费用:[/bold yellow] [bold red]{format_cost(grand['cost'])}[/bold red]"
    )
    console.print(Panel(summary_text, title="Zed pi-acp 会话消耗与成本总览", border_style="cyan", box=ROUNDED))
    console.print()

    # 2. Session details table
    if show_sessions:
        display_sessions = sessions[:limit]
        table_title = f"最近会话明细 (展示 {len(display_sessions)} / {len(sessions)} 条)"
        table = Table(title=table_title, box=ROUNDED, header_style="bold magenta", expand=False)
        table.add_column("时间", style="dim")
        table.add_column("工作区", style="cyan")
        table.add_column("标题 / 会话ID", style="bold", max_width=30, overflow="ellipsis")
        table.add_column("模型", style="blue")
        table.add_column("轮次", justify="right")
        table.add_column("输入/输出", justify="right")
        table.add_column("缓存读取", justify="right")
        table.add_column("总 Tokens", justify="right", style="green")
        table.add_column("费用", justify="right", style="yellow")

        for s in display_sessions:
            title = s.title if s.title else s.session_id[:13] + "..."
            in_out = f"{format_num(s.total_usage.input_tokens)}/{format_num(s.total_usage.output_tokens)}"
            table.add_row(
                format_time(s.updated_at),
                s.project_display,
                title,
                s.model_display,
                str(s.turns),
                in_out,
                format_num(s.total_usage.cache_read_tokens),
                format_num(s.total_usage.total_tokens),
                format_cost(s.total_usage.cost),
            )

        console.print(table)
        console.print()

    # 3. Model Aggregation Table
    if show_models:
        model_stats = aggregate_by_model(sessions)
        table = Table(title="模型消耗汇总 (按费用降序)", box=ROUNDED, header_style="bold green", expand=False)
        table.add_column("模型名称 (Provider/Model)", style="bold cyan")
        table.add_column("涉及会话", justify="right")
        table.add_column("调用轮次", justify="right")
        table.add_column("输入 Tokens", justify="right")
        table.add_column("输出 Tokens", justify="right")
        table.add_column("缓存读取 Tokens", justify="right")
        table.add_column("总 Tokens", justify="right", style="green")
        table.add_column("费用", justify="right", style="bold yellow")

        for m in model_stats:
            table.add_row(
                m["model_key"],
                str(m["session_count"]),
                str(m["turns"]),
                format_num(m["input_tokens"]),
                format_num(m["output_tokens"]),
                format_num(m["cache_read_tokens"]),
                format_num(m["total_tokens"]),
                format_cost(m["cost"]),
            )

        console.print(table)
        console.print()

    # 4. Project Aggregation Table
    if show_projects:
        proj_stats = aggregate_by_project(sessions)
        table = Table(title="工作区工程消耗汇总", box=ROUNDED, header_style="bold blue", expand=False)
        table.add_column("工作区 / 项目", style="bold cyan")
        table.add_column("会话数", justify="right")
        table.add_column("轮次", justify="right")
        table.add_column("输入 Tokens", justify="right")
        table.add_column("输出 Tokens", justify="right")
        table.add_column("缓存读取 Tokens", justify="right")
        table.add_column("总 Tokens", justify="right", style="green")
        table.add_column("费用", justify="right", style="bold yellow")

        for p in proj_stats:
            table.add_row(
                p["project"],
                str(p["session_count"]),
                str(p["turns"]),
                format_num(p["input_tokens"]),
                format_num(p["output_tokens"]),
                format_num(p["cache_read_tokens"]),
                format_num(p["total_tokens"]),
                format_cost(p["cost"]),
            )

        console.print(table)
        console.print()


def render_json(
    sessions: list[SessionStats],
    displayed_sessions: list[SessionStats] | None = None,
) -> str:
    """Format stats as clean JSON while preserving totals for all sessions."""
    if displayed_sessions is None:
        displayed_sessions = sessions
    data = {
        "summary": aggregate_grand_total(sessions),
        "by_model": aggregate_by_model(sessions),
        "by_project": aggregate_by_project(sessions),
        "sessions": [s.to_dict() for s in displayed_sessions],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)
