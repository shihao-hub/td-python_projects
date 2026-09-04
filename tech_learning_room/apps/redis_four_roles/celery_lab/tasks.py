"""示例任务：展示 Redis 通道上的三种典型用法。

- add：最普通的调用（broker 投递 → worker 执行 → backend 存结果）
- remind_later：countdown 延迟执行 —— Celery 自带的"延迟"是消息层面的
  （消息带 ETA，由 worker 侧持有等待），对比 delay_queue 的"存储层面"延迟
- flaky_import：自动重试（自愈三板斧：retry + countdown 退避 + max_retries）
"""

import time

from apps.redis_four_roles.celery_lab.app import celery_app


@celery_app.task(name="lab.add")
def add(x: int, y: int) -> int:
    """最简任务：结果写入 Redis result backend，供 AsyncResult.get() 读取。"""
    print(f"[worker] add({x}, {y}) executing")
    time.sleep(1)
    return x + y


@celery_app.task(name="lab.remind_later")
def remind_later(message: str) -> str:
    """延迟任务：调用方 apply_async(countdown=N) 后 N 秒才执行。

    注意 Celery 的 countdown 是"worker 收到消息后按 ETA 持有"，
    大量长延迟任务会占住 worker 的 unacked 消息（受 visibility_timeout
    约束），所以 creativault 对分钟级以上的延迟才自建 delay_queue。
    """
    ts = time.strftime("%H:%M:%S")
    print(f"[worker] reminder fired at {ts}: {message}")
    return f"reminded: {message}"


@celery_app.task(name="lab.flaky_import", bind=True, max_retries=3)
def flaky_import(self, source: str) -> str:
    """带重试的任务：失败自动重入队列，指数退避。"""
    print(f"[worker] flaky_import(source={source}) attempt #{self.request.retries + 1}")
    try:
        if self.request.retries < 2:
            raise ConnectionError(f"{source} 暂不可用")
        return f"imported from {source}"
    except ConnectionError as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
