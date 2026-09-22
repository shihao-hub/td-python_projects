"""watchlist 配置（config.json）读写。

结构：
{
  "check_interval_minutes": 20,
  "watchlist": [{"sec_uid": "...", "nickname": "...", "note": "..."}]
}

首次运行自动种子默认博主；原子写（临时文件 + os.replace）。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# 默认种子博主：Hucci写代码
SEED_SEC_UID = "MS4wLjABAAAA-trbwHWLAaN5ka9E6noUB_0NFl0cQwJlwB-SwiAAHjA"
SEED_NICKNAME = "Hucci写代码"

# 抖音 sec_uid 固定以 MS4wLjABAAAA 开头，其余为 base64url 字符
_SEC_UID_RE = re.compile(r"MS4wLjABAAAA[A-Za-z0-9_-]+")

# 计划任务起始时间格式（schtasks /ST 要求 HH:MM）
_HH_MM_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class WatchlistError(ValueError):
    """watchlist 输入非法（CLI 层映射为退出码 2）。"""


@dataclass
class Blogger:
    """一个被监控的博主。"""

    sec_uid: str
    nickname: str = ""
    note: str = ""


@dataclass
class Config:
    """config.json 的内存形态。"""

    check_interval_hours: int = 6
    schedule_start: str = "09:00"  # 每天首次检查时间（HH:MM）
    watchlist: list[Blogger] = field(default_factory=list)

    def find(self, sec_uid: str) -> Blogger | None:
        """按 sec_uid 查找博主。"""
        return next((b for b in self.watchlist if b.sec_uid == sec_uid), None)


def _atomic_write_json(path: Path, payload: object) -> None:
    """同目录临时文件写入后 os.replace，避免写到一半的 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def normalize_target(target: str) -> str:
    """把用户输入（主页 URL 或裸 sec_uid）归一化为 sec_uid。

    支持：
    - 完整主页 URL：https://www.douyin.com/user/MS4wLjABAAAA-xxx?query=1
    - 分享短链：https://v.douyin.com/xxx/（经一次重定向展开）
    - 裸 sec_uid：MS4wLjABAAAA-xxx
    """
    text = target.strip()
    if not text:
        raise WatchlistError("输入为空")

    if "v.douyin.com" in text:
        text = _expand_short_url(text)

    match = re.search(r"/user/([A-Za-z0-9_-]+)", text)
    if match:
        sec_uid = match.group(1)
    elif text.startswith(("http://", "https://")):
        raise WatchlistError(f"无法从 URL 中解析 sec_uid: {target}")
    else:
        sec_uid = text

    if not _SEC_UID_RE.fullmatch(sec_uid):
        raise WatchlistError(f"无法识别的博主标识: {target}")
    return sec_uid


def _expand_short_url(url: str) -> str:
    """展开 v.douyin.com 分享短链，取重定向后的真实主页 URL。"""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            final_url = response.geturl()
    except OSError as exc:
        raise WatchlistError(f"短链展开失败: {exc}") from exc
    if not final_url:
        raise WatchlistError(f"短链展开失败（无重定向目标）: {url}")
    return final_url


def load_or_init(config_path: Path) -> Config:
    """读取 config.json；不存在时以种子博主创建。"""
    if not config_path.exists():
        config = Config(
            watchlist=[Blogger(sec_uid=SEED_SEC_UID, nickname=SEED_NICKNAME)]
        )
        save_config(config_path, config)
        return config

    # utf-8-sig 兼容手工编辑可能带出的 BOM 头
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    return Config(
        check_interval_hours=_resolve_interval_hours(raw),
        schedule_start=_resolve_schedule_start(raw),
        watchlist=[
            Blogger(
                sec_uid=item["sec_uid"],
                nickname=item.get("nickname", ""),
                note=item.get("note", ""),
            )
            for item in raw.get("watchlist", [])
        ],
    )


def _resolve_interval_hours(raw: dict) -> int:
    """读取检查间隔小时数；兼容旧 check_interval_minutes 字段换算。"""
    hours = raw.get("check_interval_hours")
    if isinstance(hours, (int, float)) and hours >= 1:
        return int(hours)
    minutes = raw.get("check_interval_minutes")
    if isinstance(minutes, (int, float)) and minutes >= 60:
        return int(minutes) // 60
    return 6


def _resolve_schedule_start(raw: dict) -> str:
    """读取每日首次检查时间（HH:MM），非法值回退默认。"""
    start = str(raw.get("schedule_start", "")).strip()
    if _HH_MM_RE.fullmatch(start):
        return start
    return "09:00"


def save_config(config_path: Path, config: Config) -> None:
    """原子写 config.json。"""
    _atomic_write_json(
        config_path,
        {
            "check_interval_hours": config.check_interval_hours,
            "schedule_start": config.schedule_start,
            "watchlist": [
                {"sec_uid": b.sec_uid, "nickname": b.nickname, "note": b.note}
                for b in config.watchlist
            ],
        },
    )
