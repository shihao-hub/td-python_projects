"""抓取抖音博主主页的最新作品列表（页面内调用 web 作品接口）。

实施实证（2026-09-22 修订，详见 `plans/02-douyin-fetch-api-source.md`）：

- 计划 01 依赖的"未登录 SSR 直出作品列表"前提已失效：DOM 里
  `[data-e2e="user-post-list"]` 是空的 `scroll-list` 加"服务异常，重新刷新
  拉取数据"，页面中唯一的 `/video/` 锚点全部位于
  `FOOTER[data-e2e="page-footer"]`（页脚推荐流：随机、每轮互不重叠）。
  把它当作品会造成"真实更新永远检不出 + 偶尔推送无关视频"。
- 页面自身发起的 `/aweme/v1/web/aweme/post/` XHR，用 CDP
  `Network.getResponseBody` 取回恒为空 body；但**在同一页面上下文**用
  `Runtime.evaluate` 执行 `fetch()` 调同一接口可拿到完整 JSON
  （`status_code: 0` + `aweme_list`，newest-first）。请求由页面自身 origin
  发出、自带 ttwid 等设备指纹 cookie，因此既不需要 `a_bogus` 签名，
  也不需要登录。实证：连跑 3 轮结果完全一致；全新 profile 约 4s 成功。

取数流程：导航博主主页 → 在 `PAGE_TIMEOUT` 内轮询页面内 fetch，拿到
`aweme_list` 即返回。任何异常（HTTP 非 200 / `status_code != 0` / JSON 不可解析 /
缺 `aweme_list` / 列表为空）都抛 `FetchError` 判为抓取失败——调用方不写
state、不推送、等下轮重试。无人值守场景下"失败"远优于"猜"。

不点页面任何元素、不与页面交互；每博主一次导航，全程有界超时，
结束由 browser 层杀进程树。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .browser import CDPConnection, launch_chrome

logger = logging.getLogger("douyinnotify")

# 单博主抓取总时限（秒）
PAGE_TIMEOUT = 45.0

# 作品接口（web 端）。由页面自身 origin 调用，故无需 a_bogus 签名；
# 参数取自页面真实请求，改动需重新实证。
_POST_API_PATH = (
    "/aweme/v1/web/aweme/post/"
    "?device_platform=webapp&aid=6383&channel=channel_pc_web"
    "&sec_user_id={sec_uid}&max_cursor=0&locate_query=false"
    "&show_live_replay_strategy=1&count=18&publish_video_strategy_type=2"
    "&version_code=170400&version_name=17.4.0&cookie_enabled=true"
    "&platform=PC&downlink=10"
)

# 页面内 fetch 表达式：__URL__ 由 _build_fetch_expression 替换为 JS 字符串字面量。
# 在页面内完成解析与字段裁剪，只把最小字段回传（完整响应约 1.6MB）。
_FETCH_EXPRESSION_TEMPLATE = (
    "(async () => {"
    "  const url = __URL__;"
    "  try {"
    "    const resp = await fetch(url, {headers: {'accept': 'application/json, text/plain, */*'}});"
    "    const text = await resp.text();"
    "    let data = null;"
    "    try { data = JSON.parse(text); } catch (e) {"
    "      return JSON.stringify({http: resp.status, error: 'not-json(len=' + text.length + ')'});"
    "    }"
    "    if (!data || !Array.isArray(data.aweme_list)) {"
    "      return JSON.stringify({http: resp.status, code: data && data.status_code,"
    "        error: 'no-aweme-list'});"
    "    }"
    "    const items = data.aweme_list.map(a => ({"
    "      id: String(a.aweme_id || ''), "
    "      t: String(a.desc || '').trim(), "
    "      c: a.create_time}));"
    "    return JSON.stringify({http: resp.status, code: data.status_code, items: items});"
    "  } catch (e) {"
    "    return JSON.stringify({error: String(e)});"
    "  }"
    "})()"
)


class FetchError(RuntimeError):
    """抓取失败（风控拦截、接口异常、作品列表为空等）。

    调用方（checker）据此判定本轮该博主失败：不写 state、不推送，
    等下轮重试。
    """


@dataclass
class Aweme:
    """一条作品（检测所需最小字段）。"""

    aweme_id: str
    title: str
    created_at: int | None = None  # 作品发布时间戳（秒），接口未提供时为 None


def fetch_blogger_videos(profile_dir: Path, sec_uid: str) -> list[Aweme]:
    """抓取一个博主主页的最新作品列表。

    抛 FetchError / BrowserError 表示本轮抓取失败（调用方决定不破坏
    state、记日志等下轮重试）。
    """
    with launch_chrome(profile_dir) as chrome:
        connection = chrome.connect()
        try:
            return _fetch_with_connection(connection, sec_uid)
        except Exception:
            connection.close()
            raise


def _fetch_with_connection(connection: CDPConnection, sec_uid: str) -> list[Aweme]:
    """在已建立的 CDP 会话上完成一次导航 + 取数。"""
    target = connection.call("Target.createTarget", {"url": "about:blank"})
    target_id = str(target["targetId"])
    attached = connection.call(
        "Target.attachToTarget",
        {"targetId": target_id, "flatten": True},
    )
    session_id = str(attached["sessionId"])

    connection.call(
        "Page.navigate",
        {"url": f"https://www.douyin.com/user/{sec_uid}"},
        session_id=session_id,
    )

    deadline = time.monotonic() + PAGE_TIMEOUT
    videos = _fetch_app_videos(connection, session_id, sec_uid, deadline)
    logger.info("博主 %s 取到 %d 条作品", sec_uid, len(videos))
    return videos


def _build_fetch_expression(sec_uid: str) -> str:
    """构造页面内 fetch 表达式（URL 以 JS 字符串字面量注入，避免拼接歧义）。"""
    url = _POST_API_PATH.format(sec_uid=sec_uid)
    return _FETCH_EXPRESSION_TEMPLATE.replace("__URL__", json.dumps(url))


def _fetch_app_videos(
    connection: CDPConnection,
    session_id: str,
    sec_uid: str,
    deadline: float,
) -> list[Aweme]:
    """轮询页面内作品接口，直到拿到有效列表或超时。

    首次访问时抖音会下发风控挑战，页面上线前 fetch 拿不到数据，
    因此必须重试而非单次调用（实证：全新 profile 约 4s 后成功）。
    """
    expression = _build_fetch_expression(sec_uid)
    last_error = "未知错误"
    while time.monotonic() < deadline:
        try:
            result = connection.call(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
                session_id=session_id,
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001 - 执行上下文可能暂时不可用，下轮重试
            last_error = f"CDP 调用失败: {exc}"
            time.sleep(1.0)
            continue

        value = result.get("result", {}).get("value")
        videos, error = _parse_api_payload(value)
        if videos is not None:
            return videos
        last_error = error
        time.sleep(1.0)

    raise FetchError(f"作品接口 {PAGE_TIMEOUT:.0f}s 内未取到作品列表：{last_error}")


def _parse_api_payload(value: object) -> tuple[list[Aweme] | None, str]:
    """解析页面内 fetch 回传的紧凑 JSON（纯函数，容错）。

    返回 `(作品列表, 错误描述)`：成功时列表非空、错误描述为空串；
    "本轮还没拿到数据"时列表为 None，由调用方决定重试直至超时。
    """
    if not value:
        return None, "接口未返回内容（风控挑战未完成 / 页面尚未就绪）"
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return None, "接口回传内容不是合法 JSON"
    if not isinstance(payload, dict):
        return None, "接口回传结构异常"

    inner_error = payload.get("error")
    if inner_error:
        return None, f"页面内 fetch 异常: {inner_error}"

    http_status = payload.get("http")
    if http_status != 200:
        return None, f"作品接口 HTTP {http_status}"

    status_code = payload.get("code")
    if status_code != 0:
        return None, f"作品接口 status_code={status_code}（风控 / 参数失效）"

    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return None, "作品接口返回空列表（风控 / 需登录 / 博主无作品）"

    videos: dict[str, Aweme] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        aweme_id = str(item.get("id") or "").strip()
        if not aweme_id:
            continue
        created_at = item.get("c")
        videos[aweme_id] = Aweme(
            aweme_id=aweme_id,
            # desc 含换行（正文+话题标签），压成单行：否则飞书 markdown 列表项会被
            # 换行截断、`#话题` 还会被渲染成一行大标题
            title=" ".join(str(item.get("t") or "").split()),
            created_at=int(created_at) if isinstance(created_at, (int, float)) else None,
        )
    if not videos:
        return None, "作品接口返回的条目均缺 aweme_id"
    return list(videos.values()), ""
