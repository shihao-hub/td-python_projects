"""check 业务编排（CLI 与 MCP 共用）。

串联 watchlist → fetcher → detector → state → report；
抓取失败不破坏 state；先原子写 state 再由调用方发送
（保证"发了不重发"，崩溃最多漏发一条，可接受）。
发送动作不属于本模块：CLI 按选项决定，MCP 不发送。
"""

from __future__ import annotations

import logging
from datetime import datetime

from . import detector, fetcher, report, state, watchlist
from .paths import get_browser_profile_dir, get_config_path, get_state_path

logger = logging.getLogger("douyinnotify")


def run_check_summary(no_save: bool = False) -> report.CheckSummary:
    """执行一轮检查并返回汇总；no_save 为 True 时不写 state（探测/调试）。"""
    config = watchlist.load_or_init(get_config_path())
    current_state = state.load_state(get_state_path())

    outcomes = []
    for blogger in config.watchlist:
        try:
            videos = fetcher.fetch_blogger_videos(
                get_browser_profile_dir(), blogger.sec_uid
            )
        except Exception as exc:  # noqa: BLE001 - 单博主失败不拖垮整轮检查
            logger.error("博主 %s 抓取失败: %s", blogger.sec_uid, exc)
            outcomes.append(
                detector.CheckOutcome(
                    sec_uid=blogger.sec_uid,
                    nickname=blogger.nickname,
                    error=str(exc),
                )
            )
            continue

        outcome = detector.detect_new_videos(
            blogger.sec_uid,
            current_state.seen.get(blogger.sec_uid, set()),
            videos,
        )
        outcome.nickname = blogger.nickname
        outcomes.append(outcome)

        # 本轮抓到的 id 全部并入已见（含首检基线），作为下轮差集基准
        seen = current_state.seen.setdefault(blogger.sec_uid, set())
        seen.update(video.aweme_id for video in videos)

    summary = report.CheckSummary(
        checked_at=datetime.now().isoformat(timespec="seconds"),
        outcomes=outcomes,
    )
    logger.info(
        "检查 %d 个博主，检出 %d 条新视频",
        len(outcomes),
        summary.new_count,
    )

    if not no_save:
        state.mark_checked(current_state)
        state.save_state(get_state_path(), current_state)
    return summary
