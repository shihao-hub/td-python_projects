"""Celery 应用：broker 与 result backend 指向同一个 Redis。

从 creativault 保留的关键配置及其原因：

- task_serializer/result_serializer = json
  只走 JSON 序列化，杜绝 pickle 反序列化 RCE，跨语言也友好。

- broker_transport_options.visibility_timeout = 18000
  Redis transport 没有 AMQP 的 per-message ack 语义，靠"可见性超时"模拟：
  worker 取走消息后消息变 unacked，超时未被确认就会被其他 worker 重投。
  该值必须 > 最长任务耗时（acks_late + time_limit 场景），否则长任务
  会被双活重跑 —— creativault 在这里踩过坑（同批次双活）。

- task_acks_late + task_reject_on_worker_lost
  任务执行完才确认 + worker 进程挂掉时消息重投，近似"至少一次"投递。

- worker_prefetch_multiplier = 1
  公平调度：一次只领一个任务，避免快 worker 囤积消息导致长任务饿死。

- 环境前缀隔离队列
  同一个共享 Redis 里，dev/shawn/prod 各用各的队列名，互不串台
  （creativault 的 get_queue_name 逻辑，教学版简化为 env 前缀）。

注意：JSON 序列化意味着任务参数必须是可 JSON 化的（dict/list/str/数字）。
"""

import os

from celery import Celery
from kombu import Exchange, Queue

from apps.redis_four_roles.config import settings

_queue_prefix = os.environ.get("LAB_QUEUE_PREFIX")


def queue_name(base: str) -> str:
    """队列名永远带 lab_ 前缀；设置 LAB_QUEUE_PREFIX 后再叠加个人前缀。

    测试环境的 Redis 是共享的：队列名（即 broker 的 LIST key）撞名
    意味着别人会消费你的任务，所以前缀是硬性要求，不是可选项。
    """
    if _queue_prefix:
        return f"lab_{_queue_prefix}_{base}"
    return f"lab_{base}"


celery_app = Celery(
    "redis_four_roles_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    # 序列化：全 JSON
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 投递可靠性
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": 18000},
    # 结果 key 也加前缀（celery-task-meta-* -> lab_celery-task-meta-*）
    result_backend_transport_options={
        "visibility_timeout": 18000,
        "global_keyprefix": queue_name(""),
    },
    # 执行控制
    worker_prefetch_multiplier=1,
    task_time_limit=5 * 60,
    task_soft_time_limit=4 * 60,
    worker_max_tasks_per_child=100,
    # 结果保留 1 小时（demo 够用；线上 24h）
    result_expires=60 * 60,
    # 队列拓扑：default + high/low 优先级（生产形态，demo 只用 default）
    task_default_queue=queue_name("default"),
    task_queues=(
        Queue(queue_name("default"), Exchange(queue_name("default")), routing_key=queue_name("default")),
        Queue(queue_name("high"), Exchange(queue_name("high")), routing_key=queue_name("high")),
        Queue(queue_name("low"), Exchange(queue_name("low")), routing_key=queue_name("low")),
    ),
    # 重试
    task_default_retry_delay=5,
    task_max_retries=3,
    timezone="Asia/Shanghai",
    enable_utc=True,
)

# 注册示例任务（生产里用 autodiscover_tasks）
from apps.redis_four_roles.celery_lab import tasks  # noqa: E402,F401
