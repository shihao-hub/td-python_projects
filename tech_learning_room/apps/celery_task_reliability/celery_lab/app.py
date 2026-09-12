"""Celery 应用：broker 与 result backend 指向同一个 Redis。

可靠性参数逐条注释——它们是'任务不丢不重'的第一层防线：
- acks_late + reject_on_worker_lost：执行完才确认；worker 进程崩溃时消息重回队列
- visibility_timeout：Redis broker 的'可见性超时'，必须大于最长任务耗时，
  否则长任务还在跑就被其他 worker 重投（双活事故的经典来源）
- prefetch=1：公平调度，快 worker 不囤积消息
"""

from celery import Celery

from apps.common.settings import settings


def queue_name(base: str) -> str:
    return f"{settings.queue_prefix}_{base}"


celery_app = Celery("tlr_celery", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": 3600},
    result_backend_transport_options={"visibility_timeout": 3600},
    worker_prefetch_multiplier=1,
    task_time_limit=60,
    task_soft_time_limit=50,
    task_default_queue=queue_name("default"),
    timezone="Asia/Shanghai",
    enable_utc=True,
)

# 注册任务模块（显式 import，避免 autodiscover 的包名约定坑）
from apps.celery_task_reliability.celery_lab import tasks  # noqa: E402,F401
