"""检查结果的三种渲染：人读文本 / JSON 包络 / 飞书 post markdown。

JSON 包络固定 {"ok": .., "data": ..}（CLI 工具开发标准硬要求）；
飞书 markdown 的首行将作为 post 标题（手机通知预览显示它），
由 notifier.build_post_content 依赖该约定。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .detector import CheckOutcome

VIDEO_URL_TEMPLATE = "https://www.douyin.com/video/{aweme_id}"


@dataclass
class CheckSummary:
    """一次 check 命令的整体结果。"""

    checked_at: str
    outcomes: list[CheckOutcome] = field(default_factory=list)

    @property
    def new_count(self) -> int:
        """全部博主检出且可推送的新视频总数（基线与抖动抑制不计）。"""
        return sum(
            len(outcome.new_videos) for outcome in self.outcomes if outcome.has_news
        )


def _display_name(outcome: CheckOutcome) -> str:
    """人读展示名：优先昵称，否则 sec_uid 截断。"""
    return outcome.nickname or outcome.sec_uid[:20] + "…"


def render_text(summary: CheckSummary) -> str:
    """人读文本：检查概况 + 各博主明细。"""
    lines = [
        f"检查时间: {summary.checked_at}",
        f"检查 {len(summary.outcomes)} 个博主，检出 {summary.new_count} 条新视频",
    ]
    for outcome in summary.outcomes:
        name = _display_name(outcome)
        if not outcome.ok:
            lines.append(f"[{name}] 抓取失败: {outcome.error}")
            continue
        if outcome.first_baseline:
            lines.append(
                f"[{name}] {len(outcome.fetched)} 条作品，首检基线 {len(outcome.fetched)} 条（不推送）"
            )
            continue
        if outcome.suppressed:
            lines.append(
                f"[{name}] 检出 {len(outcome.new_videos)} 条新视频，超过阈值视为列表抖动（并入不推送）"
            )
            continue
        lines.append(
            f"[{name}] {len(outcome.fetched)} 条作品，{len(outcome.new_videos)} 条新:"
        )
        for video in outcome.new_videos:
            title = video.title or "（无标题）"
            lines.append(f"  - {title}  {VIDEO_URL_TEMPLATE.format(aweme_id=video.aweme_id)}")
    return "\n".join(lines)


def render_json(summary: CheckSummary) -> str:
    """JSON 包络输出，供 agent 分析。"""
    payload = {
        "ok": True,
        "data": {
            "checked_at": summary.checked_at,
            "new_count": summary.new_count,
            "outcomes": [
                {
                    "sec_uid": outcome.sec_uid,
                    "nickname": outcome.nickname,
                    "ok": outcome.ok,
                    "error": outcome.error,
                    "fetched_count": len(outcome.fetched),
                    "first_baseline": outcome.first_baseline,
                    "suppressed": outcome.suppressed,
                    "new_count": len(outcome.new_videos)
                    if outcome.has_news
                    else 0,
                    "new_videos": [
                        {
                            "aweme_id": video.aweme_id,
                            "title": video.title,
                            "url": VIDEO_URL_TEMPLATE.format(aweme_id=video.aweme_id),
                        }
                        for video in outcome.new_videos
                    ]
                    if outcome.has_news
                    else [],
                }
                for outcome in summary.outcomes
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def render_lark(summary: CheckSummary) -> str:
    """飞书 markdown：首行作 post 标题，其后每条新视频一行。

    仅在有可推送新视频时调用（首检基线不推送）。
    """
    title = f"抖音更新提醒：{summary.new_count} 条新视频"
    lines = [title, ""]
    for outcome in summary.outcomes:
        if not outcome.has_news:
            continue
        for video in outcome.new_videos:
            name = _display_name(outcome)
            video_title = video.title or "（无标题）"
            if len(video_title) > 60:
                video_title = video_title[:60] + "…"
            lines.append(
                f"- **{name}** 更新：[{video_title}]"
                f"({VIDEO_URL_TEMPLATE.format(aweme_id=video.aweme_id)})"
            )
    return "\n".join(lines)
