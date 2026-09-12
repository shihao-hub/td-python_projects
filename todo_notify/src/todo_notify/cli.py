"""todonotify 命令行入口。

用法：
- todonotify                     扫描 + 终端打印未完成清单
- todonotify --notify            扫描 + 发飞书（定时任务用的就是它）
- todonotify --notify --dry-run  只打印将执行的 lark-cli 命令与消息体，不实发
- todonotify --json              JSON 输出（pi / opencode 等 agent 的对接面）
- todonotify --dir <path>        覆盖默认待办目录
- todonotify install_schedule    注册 09:00 / 20:00 两个计划任务
- todonotify uninstall_schedule  卸载计划任务

退出码：0 成功（含静默不发），1 输入错误，2 飞书发送失败。
"""

from __future__ import annotations

import logging
import shlex
import sys
from pathlib import Path

import typer

from .notifier import NotifyError, build_command, send_lark
from .report import render_json, render_lark, render_text, total_open
from .scheduler import SchedulerError, install, uninstall
from .scanner import scan

# 默认待办目录
DEFAULT_TODO_DIR = Path(r"D:\Users\todo")

logger = logging.getLogger("todo_notify")

app = typer.Typer(
    help="扫描待办 Markdown 复选框并推送飞书提醒。",
    add_completion=False,
)


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


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    notify: bool = typer.Option(False, "--notify", help="扫描后发送飞书通知"),
    dry_run: bool = typer.Option(False, "--dry-run", help="配合 --notify：只打印将执行的命令与消息体，不实发"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 输出结构化结果，供 agent 分析"),
    todo_dir: Path = typer.Option(DEFAULT_TODO_DIR, "--dir", help="待办目录，默认 D:\\Users\\todo"),
) -> None:
    """默认行为：扫描待办目录并列出未完成任务。"""
    _prepare_stream()
    try:
        from .applog import setup_logging

        setup_logging()
    except OSError:
        pass  # 日志目录创建失败不阻断主流程

    if ctx.invoked_subcommand is not None:
        return  # 本次调用走子命令，不执行扫描

    if not todo_dir.is_dir():
        _print(f"待办目录不存在: {todo_dir}", file=sys.stderr)
        raise typer.Exit(code=1)

    reports = scan(todo_dir)
    logger.info("扫描 %s：%d 个文件，%d 条未完成", todo_dir, len(reports), total_open(reports))

    if json_output:
        _print(render_json(reports, todo_dir))
        return

    _print(render_text(reports))

    if not notify:
        return
    if total_open(reports) == 0:
        logger.info("无未完成任务，静默不发")
        return

    markdown = render_lark(reports)
    if dry_run:
        _print("[dry-run] 将执行：")
        _print(shlex.join(build_command(markdown)))
        _print("[dry-run] 消息体：")
        _print(markdown)
        return

    try:
        message_id = send_lark(markdown)
    except NotifyError as exc:
        logger.error("飞书发送失败: %s", exc)
        _print(f"飞书发送失败: {exc}", file=sys.stderr)
        raise typer.Exit(code=2) from None
    logger.info("飞书通知已发送 message_id=%s", message_id)
    _print(f"已发送飞书通知（message_id={message_id}）")


@app.command("install_schedule")
def install_schedule() -> None:
    """注册早晚两个每日计划任务（09:00 / 20:00）。"""
    _prepare_stream()
    try:
        created = install()
    except SchedulerError as exc:
        _print(str(exc), file=sys.stderr)
        raise typer.Exit(code=1) from None
    for task_name in created:
        _print(f"已创建计划任务: {task_name}")


@app.command("uninstall_schedule")
def uninstall_schedule() -> None:
    """卸载早晚两个计划任务。"""
    _prepare_stream()
    try:
        removed = uninstall()
    except SchedulerError as exc:
        _print(str(exc), file=sys.stderr)
        raise typer.Exit(code=1) from None
    if removed:
        for task_name in removed:
            _print(f"已删除计划任务: {task_name}")
    else:
        _print("没有已存在的计划任务")
