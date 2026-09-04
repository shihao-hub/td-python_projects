"""角色三：延迟队列 —— 数据模型、key 模板与默认参数。

对应 creativault 的 src/infrastructure/delay_queue/models.py。
存储结构（每个队列 4 个 key）：

    dq:pending:{queue}     ZSET   member=task_id, score=到期时间戳(ms)   ← 时间轮
    dq:tasks:{queue}       HASH   field=task_id,   value=payload JSON    ← 载荷
    dq:processing:{queue}  ZSET   member=task_id, score=领取时间戳(ms)   ← 处理中
    dq:wakeup:{queue}      LIST   LPUSH '1' 唤醒 / BLPOP 阻塞等待        ← 信号量
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DelayTask:
    """延时任务（enqueue 返回给调用方的视图）。"""

    task_id: str
    queue: str
    payload: dict[str, Any]
    enqueued_at: int
    expire_at: int


def pending_key(queue: str) -> str:
    return f"lab:dq:pending:{queue}"


def tasks_key(queue: str) -> str:
    return f"lab:dq:tasks:{queue}"


def processing_key(queue: str) -> str:
    return f"lab:dq:processing:{queue}"


def wakeup_key(queue: str) -> str:
    return f"lab:dq:wakeup:{queue}"


DEFAULT_BATCH_SIZE = 10
"""每轮 dequeue 最多取出的到期任务数。"""

DEFAULT_BLPOP_TIMEOUT_S = 1.0
"""BLPOP 阻塞超时；超时后照样检查一轮到期任务（兜底，防唤醒信号丢失）。"""

DEFAULT_RECLAIM_TIMEOUT_S = 30.0
"""processing 中超过该秒数未 ACK 的任务会被 reclaim 放回 pending（教学版调小，线上 600）。"""

DEFAULT_RECLAIM_INTERVAL_S = 10.0
"""reclaim 扫描间隔（教学版调小，线上 60）。"""

DEFAULT_DISPATCHER_STOP_TIMEOUT_S = 5.0
"""stop() 等待协程退出的超时。"""
