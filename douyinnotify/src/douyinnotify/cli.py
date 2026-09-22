"""douyinnotify 命令行入口。

用法：
- douyinnotify                     立即检查一次所有博主（等价于 check）
- douyinnotify check --notify      检查 + 发现新视频时发飞书（定时任务用的就是它）
- douyinnotify check --dry-run     配合 --notify：只打印将执行的 lark-cli 命令与消息体
- douyinnotify list                查看监控列表与上次检查快照
- douyinnotify add <主页URL|sec_uid>    添加博主
- douyinnotify remove <sec_uid>    移除博主
- douyinnotify install_schedule    注册计划任务（按 config.json 间隔定时跑）
- douyinnotify uninstall_schedule  卸载计划任务
- douyinnotify schema              导出 MCP 工具契约目录 JSON
- douyinnotify mcp                 启动 MCP stdio server

退出码：0 成功（含静默不发），2 参数错误，1 其他失败（含发送失败）。
"""

from __future__ import annotations

import json
import logging
import shlex
import sys

import typer

from . import checker, notifier, report, scheduler, state, watchlist
from .paths import get_config_path, get_state_path

app = typer.Typer(
    help="监控抖音博主更新并推送飞书提醒。",
    add_completion=False,
)

logger = logging.getLogger("douyinnotify")


def _prepare_stream() -> None:
    """终端兼容兜底：管道/计划任务下强制 UTF-8 输出，异常字符降级为 ? 不抛错。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _print(msg: str = "", *, file=None) -> None:
    """无人值守（计划任务无控制台）时 stdout 句柄无效，打印失败不致命。"""
    try:
        print(msg, file=file)
    except OSError:
        pass


def _not_implemented(name: str) -> None:
    """骨架阶段的子命令占位，后续任务逐个填充实现。"""
    _print(f"{name} 尚未实现", file=sys.stderr)
    raise typer.Exit(code=1)


def run_check(
    notify: bool,
    dry_run: bool,
    json_output: bool,
    no_save: bool,
) -> None:
    """check 主流程输出与发送编排（业务见 checker.run_check_summary）。

    失败语义（CLI 标准）：抓取失败不破坏 state、等下轮重试；
    先原子写 state 再发消息（保证"发了不重发"，崩溃最多漏发一条，可接受）。
    """
    summary = checker.run_check_summary(no_save=no_save)

    send_failed: str | None = None
    if notify and summary.new_count > 0:
        markdown = report.render_lark(summary)
        if dry_run:
            _print("[dry-run] 将执行：")
            _print(shlex.join(notifier.build_command(markdown)))
            _print("[dry-run] 消息体：")
            _print(markdown)
        else:
            try:
                message_id = notifier.send_lark(markdown)
            except notifier.NotifyError as exc:
                logger.error("飞书发送失败: %s", exc)
                send_failed = str(exc)
            else:
                logger.info("飞书通知已发送 message_id=%s", message_id)
    elif notify:
        logger.info("无新视频，静默不发")

    outcomes = summary.outcomes
    all_failed = bool(outcomes) and all(not outcome.ok for outcome in outcomes)

    if json_output:
        envelope = json.loads(report.render_json(summary))
        if send_failed:
            envelope["ok"] = False
            envelope["error"] = f"飞书发送失败: {send_failed}"
        elif all_failed:
            # 与文本路径口径一致：全部博主失败即失败，不能让包络谎报成功
            envelope["ok"] = False
            envelope["error"] = "全部博主抓取失败"
        _print(json.dumps(envelope, ensure_ascii=False))
        if send_failed or all_failed:
            raise typer.Exit(code=1)
        return

    _print(report.render_text(summary))
    if send_failed:
        _print(f"飞书发送失败: {send_failed}", file=sys.stderr)
        raise typer.Exit(code=1)

    if all_failed:
        raise typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    notify: bool = typer.Option(False, "--notify", help="发现新视频时发送飞书通知"),
    dry_run: bool = typer.Option(False, "--dry-run", help="配合 --notify：只打印将执行的命令与消息体，不实发"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 包络输出结构化结果，供 agent 分析"),
    no_save: bool = typer.Option(False, "--no-save", help="只检查不写 state（探测/调试用）"),
) -> None:
    """默认行为：立即检查一次所有博主（等价于 check）。"""
    _prepare_stream()
    try:
        from .applog import setup_logging

        setup_logging()
    except OSError:
        pass  # 日志目录创建失败不阻断主流程

    if ctx.invoked_subcommand is not None:
        return  # 本次调用走子命令，不执行检查
    run_check(notify, dry_run, json_output, no_save)


@app.command("check")
def check(
    notify: bool = typer.Option(False, "--notify", help="发现新视频时发送飞书通知"),
    dry_run: bool = typer.Option(False, "--dry-run", help="配合 --notify：只打印将执行的命令与消息体，不实发"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 包络输出结构化结果，供 agent 分析"),
    no_save: bool = typer.Option(False, "--no-save", help="只检查不写 state（探测/调试用）"),
) -> None:
    """立即检查一次所有博主，发现新视频可选推送飞书。"""
    _prepare_stream()
    run_check(notify, dry_run, json_output, no_save)


def _display_name(blogger: watchlist.Blogger) -> str:
    """人读展示名：优先昵称，否则 sec_uid 截断。"""
    return blogger.nickname or blogger.sec_uid[:20] + "…"


@app.command("list")
def list_watch(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 包络输出"),
) -> None:
    """查看监控列表与上次检查快照。"""
    _prepare_stream()
    config = watchlist.load_or_init(get_config_path())
    current_state = state.load_state(get_state_path())
    data = {
        "check_interval_hours": config.check_interval_hours,
        "schedule_start": config.schedule_start,
        "last_check": current_state.last_check,
        "watchlist": [
            {
                "sec_uid": blogger.sec_uid,
                "nickname": blogger.nickname,
                "note": blogger.note,
                "seen_count": len(current_state.seen.get(blogger.sec_uid, ())),
            }
            for blogger in config.watchlist
        ],
    }
    if json_output:
        _print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
        return

    lines = [
        f"检查间隔: {config.check_interval_hours} 小时（{config.schedule_start} 起每 {config.check_interval_hours} 小时）",
        f"上次检查: {current_state.last_check or '从未'}",
        f"监控 {len(config.watchlist)} 个博主:",
    ]
    for blogger in config.watchlist:
        seen_count = len(current_state.seen.get(blogger.sec_uid, ()))
        note = f"（{blogger.note}）" if blogger.note else ""
        lines.append(
            f"  - {_display_name(blogger)}  {blogger.sec_uid[:24]}…  "
            f"已见 {seen_count} 个视频{note}"
        )
    _print("\n".join(lines))


@app.command("add")
def add(
    target: str = typer.Argument(..., help="博主主页 URL、分享短链或裸 sec_uid"),
    note: str = typer.Option("", "--note", help="备注"),
) -> None:
    """添加监控博主。"""
    _prepare_stream()
    try:
        sec_uid = watchlist.normalize_target(target)
    except watchlist.WatchlistError as exc:
        _print(str(exc), file=sys.stderr)
        raise typer.Exit(code=2) from None

    config = watchlist.load_or_init(get_config_path())
    if config.find(sec_uid) is not None:
        _print(f"已在监控列表中: {sec_uid}")
        return

    config.watchlist.append(watchlist.Blogger(sec_uid=sec_uid, note=note))
    watchlist.save_config(get_config_path(), config)
    logger.info("添加博主 %s", sec_uid)
    _print(f"已添加: {sec_uid}")


@app.command("remove")
def remove(
    sec_uid: str = typer.Argument(..., help="要移除的博主 sec_uid（或其主页 URL）"),
) -> None:
    """移除监控博主。"""
    _prepare_stream()
    try:
        normalized = watchlist.normalize_target(sec_uid)
    except watchlist.WatchlistError as exc:
        _print(str(exc), file=sys.stderr)
        raise typer.Exit(code=2) from None

    config = watchlist.load_or_init(get_config_path())
    blogger = config.find(normalized)
    if blogger is None:
        _print(f"不在监控列表中: {normalized}", file=sys.stderr)
        raise typer.Exit(code=2) from None

    config.watchlist.remove(blogger)
    watchlist.save_config(get_config_path(), config)
    current_state = state.load_state(get_state_path())
    current_state.seen.pop(normalized, None)
    state.save_state(get_state_path(), current_state)
    logger.info("移除博主 %s", normalized)
    _print(f"已移除: {normalized}")


@app.command("install_schedule")
def install_schedule() -> None:
    """注册计划任务（默认 9 点开始每 6 小时，读 config.json 的
    check_interval_hours 与 schedule_start）。"""
    _prepare_stream()
    try:
        interval, start = scheduler.install()
    except scheduler.SchedulerError as exc:
        _print(str(exc), file=sys.stderr)
        raise typer.Exit(code=1) from None
    _print(
        f"已创建计划任务: {scheduler.TASK_NAME}（{start} 起每 {interval} 小时）"
    )


@app.command("uninstall_schedule")
def uninstall_schedule() -> None:
    """卸载计划任务（幂等：任务不存在时提示而非报错）。"""
    _prepare_stream()
    try:
        removed = scheduler.uninstall()
    except scheduler.SchedulerError as exc:
        _print(str(exc), file=sys.stderr)
        raise typer.Exit(code=1) from None
    if removed:
        _print(f"已删除计划任务: {scheduler.TASK_NAME}")
    else:
        _print("没有已存在的计划任务")


@app.command("schema")
def schema() -> None:
    """导出 MCP 工具契约目录 JSON（与 mcp 注册同源，不启动业务依赖）。"""
    _prepare_stream()
    from . import schema_export

    _print(schema_export.export_contracts())


@app.command("mcp")
def mcp() -> None:
    """启动 MCP stdio server（供 agent 检查入口）。"""
    _prepare_stream()
    from . import mcp_server

    mcp_server.run()
