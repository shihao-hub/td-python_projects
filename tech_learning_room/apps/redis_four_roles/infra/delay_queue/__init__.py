"""角色三：延迟队列包。

一个 Redis 用三个 ZSET/HASH/LIST key 组合实现"到点才执行"：
- ZSET 当时间轮（score=到期时间）
- HASH 存载荷
- LIST 当唤醒信号（LPUSH/BLPOP），让 Dispatcher 免于空转轮询
- processing ZSET + reclaim 兜底处理方崩溃
"""

from apps.redis_four_roles.infra.delay_queue.dispatcher import (
    DelayDispatcher,
    DelayDispatcherRegistry,
    delay_dispatcher_registry,
)
from apps.redis_four_roles.infra.delay_queue.manager import DelayQueueManager, delay_queue
from apps.redis_four_roles.infra.delay_queue.models import DelayTask

__all__ = [
    "DelayTask",
    "DelayQueueManager",
    "delay_queue",
    "DelayDispatcher",
    "DelayDispatcherRegistry",
    "delay_dispatcher_registry",
]
