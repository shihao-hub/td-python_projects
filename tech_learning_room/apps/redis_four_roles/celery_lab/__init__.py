"""celery_lab — 角色二：Celery 把 Redis 当消息通道 + 结果后端。

对应 creativault 的 src/worker/celery_app.py（去掉了 Sentry/告警/Kafka
等周边，保留 Redis 相关的核心配置项）。

Redis 在这里承担两个通道：
- broker：任务消息经 Redis LIST 投递（kombu 的 redis transport）
- result backend：任务执行结果写回 Redis（AsyncResult.get() 从这读）
"""

from apps.redis_four_roles.celery_lab.app import celery_app

__all__ = ["celery_app"]
