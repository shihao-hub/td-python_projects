"""Celery demo 生产者：向 Redis broker 发任务，从 Redis backend 收结果。

先在另一个终端起 worker（Windows 必须 --pool=solo）：

    uv run celery -A apps.redis_four_roles.celery_lab worker --pool=solo -l info

然后运行本脚本：

    uv run python -m apps.redis_four_roles.celery_demo
"""

import time

from celery.result import AsyncResult

from apps.redis_four_roles.celery_lab.tasks import add, flaky_import, remind_later


def demo_basic() -> None:
    print("-- 普通 invoke：broker 投递 -> worker 执行 -> backend 存结果 --")
    result: AsyncResult = add.delay(2, 3)
    print(f"    task_id={result.id} state={result.state}")
    value = result.get(timeout=30)
    print(f"    add(2, 3) = {value}")


def demo_countdown() -> None:
    print("-- countdown 延迟：消息带 ETA，由 worker 侧持有等待 --")
    t0 = time.time()
    result = remind_later.apply_async(["倒计时任务"], countdown=5)
    print(f"    {time.strftime('%H:%M:%S')} 已投递，5 秒后执行 ...")
    value = result.get(timeout=30)
    print(f"    {time.time() - t0:.1f}s 后完成：{value}")


def demo_retry() -> None:
    print("-- 自动重试：前两次失败，指数退避后成功 --")
    result = flaky_import.delay("crm")
    value = result.get(timeout=60, propagate=False)
    print(f"    最终结果：{value}")


def main() -> None:
    demo_basic()
    print()
    demo_countdown()
    print()
    demo_retry()
    print("\n（此时可用 redis-cli 看到 celery@ / unacked 等 broker key）")


if __name__ == "__main__":
    main()
