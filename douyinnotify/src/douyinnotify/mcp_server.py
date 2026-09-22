"""MCP stdio server：把 douyinnotify 能力暴露给 agent。

工具与 CLI 能力一一对应（CLI 工具开发标准双入口要求）；
check 在 MCP 侧只做检查与结构化返回，不发送飞书（--notify 仅 CLI）。
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from . import checker, report, state, watchlist
from .paths import get_config_path, get_state_path

mcp = FastMCP("douyinnotify")


@mcp.tool(name="douyinnotify.check")
def check_tool(no_save: bool = False) -> dict:
    """立即检查一次所有博主，返回抓取与新视频检测结果（不发送飞书）。"""
    summary = checker.run_check_summary(no_save=no_save)
    return json.loads(report.render_json(summary))


@mcp.tool(name="douyinnotify.list")
def list_tool() -> dict:
    """查看监控列表、检查间隔与上次检查快照。"""
    config = watchlist.load_or_init(get_config_path())
    current_state = state.load_state(get_state_path())
    return {
        "ok": True,
        "data": {
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
        },
    }


@mcp.tool(name="douyinnotify.add")
def add_tool(target: str, note: str = "") -> dict:
    """添加监控博主；target 支持主页 URL、分享短链或裸 sec_uid。"""
    try:
        sec_uid = watchlist.normalize_target(target)
    except watchlist.WatchlistError as exc:
        return {"ok": False, "error": str(exc)}

    config = watchlist.load_or_init(get_config_path())
    if config.find(sec_uid) is not None:
        return {"ok": True, "data": {"sec_uid": sec_uid, "already_exists": True}}
    config.watchlist.append(watchlist.Blogger(sec_uid=sec_uid, note=note))
    watchlist.save_config(get_config_path(), config)
    return {"ok": True, "data": {"sec_uid": sec_uid, "already_exists": False}}


@mcp.tool(name="douyinnotify.remove")
def remove_tool(sec_uid: str) -> dict:
    """移除监控博主（接受 sec_uid 或其主页 URL）。"""
    try:
        normalized = watchlist.normalize_target(sec_uid)
    except watchlist.WatchlistError as exc:
        return {"ok": False, "error": str(exc)}

    config = watchlist.load_or_init(get_config_path())
    blogger = config.find(normalized)
    if blogger is None:
        return {"ok": False, "error": f"不在监控列表中: {normalized}"}
    config.watchlist.remove(blogger)
    watchlist.save_config(get_config_path(), config)
    current_state = state.load_state(get_state_path())
    current_state.seen.pop(normalized, None)
    state.save_state(get_state_path(), current_state)
    return {"ok": True, "data": {"sec_uid": normalized, "removed": True}}


def run() -> None:
    """启动 stdio server（阻塞运行，stdout 为 MCP 协议通道）。"""
    mcp.run()
