"""新视频检测：已见集合与当前抓取列表的差集。

判定 = aweme_id 集合差（current 减 state 已见）；首次检查（seen 为空）
只记基线不推送，避免把博主全部历史视频当成"新"轰炸用户。
每轮检查后由调用方把当前抓到的 id 全部并入 seen——即使本轮不推送
（首检基线、阈值抑制），也要落盘，作为下一轮的差集基准。

阈值抑制：单轮新增超过 MAX_NEWS_PER_CHECK 时只并入 seen 不推送。
该值原本用于对抗“未登录 SSR 抽样抖动”（见计划 01），但数据源已换成
可信的作品接口（见计划 02），抖动不存在了，此阈值退化为**异常放大**
的兜底保护（例如 state 被清空 / 误删导致一次性大量“新增”）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fetcher import Aweme

# 单博主单轮新增推送阈值：超过则视为异常放大（如 state 被清空 / 误删），
# 只并入 seen 不推送。数据源换成可信的作品接口后，该值不再用于对抗列表
# 抖动（见 plans/02-douyin-fetch-api-source.md），仅作兜底保护。
MAX_NEWS_PER_CHECK = 10


@dataclass
class CheckOutcome:
    """单博主的检查结果。"""

    sec_uid: str
    nickname: str = ""
    fetched: list[Aweme] = field(default_factory=list)
    new_videos: list[Aweme] = field(default_factory=list)
    first_baseline: bool = False
    suppressed: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        """抓取是否成功（失败不破坏 state，等下轮重试）。"""
        return self.error is None

    @property
    def has_news(self) -> bool:
        """是否检出需要推送的新视频（基线与抖动抑制均不算）。"""
        return bool(self.new_videos) and not self.first_baseline and not self.suppressed


def detect_new_videos(sec_uid: str, seen_ids: set[str], current: list[Aweme]) -> CheckOutcome:
    """对单个博主计算差集；seen_ids 为空视作首次基线。"""
    first_baseline = not seen_ids
    new_videos = [video for video in current if video.aweme_id not in seen_ids]
    return CheckOutcome(
        sec_uid=sec_uid,
        fetched=current,
        new_videos=new_videos,
        first_baseline=first_baseline,
        suppressed=len(new_videos) > MAX_NEWS_PER_CHECK,
    )
