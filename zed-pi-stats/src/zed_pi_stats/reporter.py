"""Reporting and aggregation views for zed-pi-stats."""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.box import ROUNDED
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zed_pi_stats.models import (
    SessionStats,
    TokenUsage,
    get_full_schema,
    get_model_schema,
    get_project_schema,
    get_session_schema,
)

# Ensure stdout/stderr handles UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def format_tokens(n: int, raw: bool = False) -> str:
    """Format token count. Uses K/M/B abbreviations by default for compact terminal display."""
    if raw or n < 1_000:
        return f"{n:,}"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    return f"{n / 1_000:.1f}K"


def format_in_out(inp: int, out: int, raw: bool = False) -> str:
    """Format input/output token pair compactly."""
    if raw:
        return f"{inp:,}/{out:,}"
    return f"{format_tokens(inp)}/{format_tokens(out)}"


def format_cost(cost: float) -> str:
    """Format cost nicely (e.g. $0.0012, $1.25)."""
    if cost == 0:
        return "$0.00"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def format_time(ts: str) -> str:
    """Format ISO timestamp to shorter human-friendly time (MM-DD HH:MM)."""
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
    wide: bool = False,
    raw_numbers: bool = False,
    console: Console | None = None,
) -> None:
    """Print beautifully formatted Rich tables to terminal with unified width alignment."""
    if console is None:
        console = get_console()

    if not sessions:
        console.print("[yellow]未找到匹配的 Zed pi-acp 会话记录。[/yellow]")
        return

    grand = aggregate_grand_total(sessions)
    term_width = console.width or 85
    is_wide = wide or (term_width >= 115)

    # 1. Grand Total Summary Panel (100% width aligned)
    in_str = format_tokens(grand['input_tokens'], raw=raw_numbers)
    out_str = format_tokens(grand['output_tokens'], raw=raw_numbers)
    cache_str = format_tokens(grand['cache_read_tokens'], raw=raw_numbers)
    tot_str = format_tokens(grand['total_tokens'], raw=raw_numbers)

    summary_text = (
        f"[bold cyan]会话总数:[/bold cyan] {grand['session_count']}    "
        f"[bold cyan]交互轮次:[/bold cyan] {grand['turns']}    "
        f"[bold green]总消耗 Tokens:[/bold green] {tot_str} "
        f"([dim]输入:[/dim] {in_str}, [dim]输出:[/dim] {out_str}, [dim]缓存读:[/dim] {cache_str})    "
        f"[bold yellow]总费用:[/bold yellow] [bold red]{format_cost(grand['cost'])}[/bold red]"
    )
    console.print(Panel(summary_text, title="Zed pi-acp 会话消耗与成本总览", border_style="cyan", box=ROUNDED, expand=True))
    console.print()

    # 2. Session details table (expand=True, aligned with panel)
    if show_sessions:
        display_sessions = sessions[:limit]
        table_title = f"最近会话明细 (展示 {len(display_sessions)} / {len(sessions)} 条)"
        table = Table(
            title=table_title,
            box=ROUNDED,
            header_style="bold magenta",
            expand=True,
            pad_edge=False,
            collapse_padding=True,
        )

        table.add_column("时间", style="dim", no_wrap=True)
        # Allow workspace column up to 22 chars so common folder names show completely
        table.add_column("工作区", style="cyan", no_wrap=True, min_width=8, max_width=22, overflow="ellipsis")
        # Elastic column: absorb extra width so titles can stretch as wide as the terminal allows
        table.add_column("会话/标题", style="bold", min_width=10, ratio=1, overflow="ellipsis")
        table.add_column("模型", style="blue", no_wrap=True, max_width=13, overflow="ellipsis")
        table.add_column("轮次", justify="right", no_wrap=True)

        if is_wide:
            table.add_column("输入/输出", justify="right", no_wrap=True)
            table.add_column("缓存读取", justify="right", no_wrap=True)

        table.add_column("Tokens", justify="right", style="green", no_wrap=True)
        table.add_column("费用", justify="right", style="yellow", no_wrap=True)

        for s in display_sessions:
            title = s.title_display
            tot_tok = format_tokens(s.total_usage.total_tokens, raw=raw_numbers)
            cost_str = format_cost(s.total_usage.cost)

            row = [
                format_time(s.updated_at),
                s.project_display,
                title,
                s.model_display,
                str(s.turns),
            ]
            if is_wide:
                row.append(format_in_out(s.total_usage.input_tokens, s.total_usage.output_tokens, raw=raw_numbers))
                row.append(format_tokens(s.total_usage.cache_read_tokens, raw=raw_numbers))

            row.extend([tot_tok, cost_str])
            table.add_row(*row)

        console.print(table)
        console.print()

    # 3. Model Aggregation Table (expand=True, aligned with panel)
    if show_models:
        model_stats = aggregate_by_model(sessions)
        table = Table(
            title="模型消耗汇总 (按费用降序)",
            box=ROUNDED,
            header_style="bold green",
            expand=True,
            pad_edge=False,
            collapse_padding=True,
        )
        # Elastic column: model name stretches to fill width
        table.add_column("模型名称", style="bold cyan", min_width=12, ratio=1, overflow="ellipsis")
        table.add_column("会话数", justify="right", no_wrap=True)
        table.add_column("调用轮次", justify="right", no_wrap=True)

        if is_wide:
            table.add_column("输入 Tokens", justify="right", no_wrap=True)
            table.add_column("输出 Tokens", justify="right", no_wrap=True)
            table.add_column("缓存读取", justify="right", no_wrap=True)
        else:
            table.add_column("输入/输出", justify="right", no_wrap=True)
            table.add_column("缓存读", justify="right", no_wrap=True)

        table.add_column("Tokens", justify="right", style="green", no_wrap=True)
        table.add_column("费用", justify="right", style="bold yellow", no_wrap=True)

        for m in model_stats:
            m_name = m["model_key"]
            if not is_wide and "/" in m_name:
                m_name = m_name.split("/")[-1]

            row = [
                m_name,
                str(m["session_count"]),
                str(m["turns"]),
            ]
            if is_wide:
                row.append(format_tokens(m["input_tokens"], raw=raw_numbers))
                row.append(format_tokens(m["output_tokens"], raw=raw_numbers))
                row.append(format_tokens(m["cache_read_tokens"], raw=raw_numbers))
            else:
                row.append(format_in_out(m["input_tokens"], m["output_tokens"], raw=raw_numbers))
                row.append(format_tokens(m["cache_read_tokens"], raw=raw_numbers))

            row.extend([
                format_tokens(m["total_tokens"], raw=raw_numbers),
                format_cost(m["cost"]),
            ])
            table.add_row(*row)

        console.print(table)
        console.print()

    # 4. Project Aggregation Table (expand=True, aligned with panel)
    if show_projects:
        proj_stats = aggregate_by_project(sessions)
        table = Table(
            title="工作区工程消耗汇总",
            box=ROUNDED,
            header_style="bold blue",
            expand=True,
            pad_edge=False,
            collapse_padding=True,
        )
        # Elastic column: project name stretches to fill width
        table.add_column("工作区 / 项目", style="bold cyan", min_width=12, ratio=1, overflow="ellipsis")
        table.add_column("会话数", justify="right", no_wrap=True)
        table.add_column("轮次", justify="right", no_wrap=True)

        if is_wide:
            table.add_column("输入 Tokens", justify="right", no_wrap=True)
            table.add_column("输出 Tokens", justify="right", no_wrap=True)
            table.add_column("缓存读取", justify="right", no_wrap=True)
        else:
            table.add_column("输入/输出", justify="right", no_wrap=True)
            table.add_column("缓存读", justify="right", no_wrap=True)

        table.add_column("Tokens", justify="right", style="green", no_wrap=True)
        table.add_column("费用", justify="right", style="bold yellow", no_wrap=True)

        for p in proj_stats:
            row = [
                p["project"],
                str(p["session_count"]),
                str(p["turns"]),
            ]
            if is_wide:
                row.append(format_tokens(p["input_tokens"], raw=raw_numbers))
                row.append(format_tokens(p["output_tokens"], raw=raw_numbers))
                row.append(format_tokens(p["cache_read_tokens"], raw=raw_numbers))
            else:
                row.append(format_in_out(p["input_tokens"], p["output_tokens"], raw=raw_numbers))
                row.append(format_tokens(p["cache_read_tokens"], raw=raw_numbers))

            row.extend([
                format_tokens(p["total_tokens"], raw=raw_numbers),
                format_cost(p["cost"]),
            ])
            table.add_row(*row)

        console.print(table)
        console.print()


def render_projected_json(
    sessions: list[SessionStats],
    by_model: bool = False,
    by_project: bool = False,
    by_session: bool = False,
    limit: int = 10,
) -> str:
    """Format stats as clean JSON projected to the selected view."""
    summary_data = aggregate_grand_total(sessions)

    if by_model and not by_project and not by_session:
        data = {
            "summary": summary_data,
            "by_model": aggregate_by_model(sessions),
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    if by_project and not by_model and not by_session:
        data = {
            "summary": summary_data,
            "by_project": aggregate_by_project(sessions),
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    if by_session and not by_model and not by_project:
        display_sessions = sessions[:limit] if limit > 0 else sessions
        data = {
            "summary": summary_data,
            "sessions": [s.to_dict() for s in display_sessions],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    # Full report (default)
    data = {
        "summary": summary_data,
        "by_model": aggregate_by_model(sessions),
        "by_project": aggregate_by_project(sessions),
        "sessions": [s.to_dict() for s in (sessions[:limit] if limit > 0 else sessions)],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def get_projected_schema(
    by_model: bool = False,
    by_project: bool = False,
    by_session: bool = False,
) -> dict[str, Any]:
    """Return JSON Schema perfectly paired with the projected JSON output."""
    if by_model and not by_project and not by_session:
        return get_model_schema()
    if by_project and not by_model and not by_session:
        return get_project_schema()
    if by_session and not by_model and not by_project:
        return get_session_schema()
    return get_full_schema()
