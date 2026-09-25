"""各 ACP 智能体的数据采集器包。

每个采集器模块暴露统一契约：
    collect(days: int | None = None) -> list[SessionStats]
"""

from zedagentstats.collectors._zed import get_zed_threads

__all__ = ["get_zed_threads"]
