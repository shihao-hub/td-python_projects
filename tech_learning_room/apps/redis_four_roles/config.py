"""集中配置：从 .env / 环境变量读取，整个 app 只此一处。"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# 项目根（tech_learning_room/）下的 .env 优先级低于进程已设置的环境变量
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))


@dataclass
class Settings:
    """app 全局配置。

    生产项目里这部分通常来自 apollo / nacos 等配置中心（creativault 的
    setting.redis_url 就是这么来的），教学版退化为 .env + 默认值。

    共享测试环境约定：所有写入 Redis 的 key 一律带 lab: / lab_ 前缀，
    Celery 队列与结果 key 再叠加 LAB_QUEUE_PREFIX 做个人隔离。
    """

    redis_url: str = field(
        default_factory=lambda: os.environ.get(
            "REDIS_URL", "redis://localhost:6379/0"
        )
    )

    queue_prefix: str = field(
        default_factory=lambda: os.environ.get("LAB_QUEUE_PREFIX", "")
    )

    # 缓存默认 TTL（秒）
    cache_default_ttl: int = 60

    # 延迟队列默认参数（与 creativault 保持一致的取值）
    dq_batch_size: int = 10
    dq_blpop_timeout_s: float = 1.0
    dq_reclaim_timeout_s: float = 30.0
    dq_reclaim_interval_s: float = 10.0


settings = Settings()
