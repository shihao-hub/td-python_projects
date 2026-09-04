"""四角色联跑 demo（缓存 / 限流 / 延迟队列 三个角色的 async 演示）。

运行前提：本机（或 REDIS_URL 指向的）Redis 可达。

    uv run python -m apps.redis_four_roles

Celery 角色单独演示（需要另开终端起 worker）：

    uv run celery -A apps.redis_four_roles.celery_lab worker --pool=solo -l info
    uv run python -m apps.redis_four_roles.celery_demo
"""

import asyncio
import time

from apps.redis_four_roles.infra.cache import CacheManager
from apps.redis_four_roles.infra.delay_queue import DelayDispatcher, delay_queue
from apps.redis_four_roles.infra.rate_limiter import SlidingWindowRateLimiter, WindowRule
from apps.redis_four_roles.infra.redis_manager import redis_manager


class FourRolesDemo:
    """把三种 Redis 角色（缓存/限流/延迟队列）串成一场可观察的演示。

    刻意用 OOP 组织：每个角色一个字段，演示方法一个角色一段，
    对应"一个 Redis 实例、多种访问模式"的主题。
    """

    def __init__(self) -> None:
        self.cache = CacheManager()
        self.limiter = SlidingWindowRateLimiter()

    async def run(self) -> None:
        redis_manager.init_pool()
        try:
            if not await redis_manager.ping():
                print("!! Redis 不可达，请先启动 Redis（或设置 REDIS_URL）")
                return
            print("== Redis 连接正常，开始演示 ==\n")

            await self._demo_cache()
            await self._demo_rate_limiter()
            await self._demo_delay_queue()

            print("\n== 演示结束 ==")
        finally:
            await redis_manager.close()

    # ──────────────────────────────────────────
    # 角色一：缓存
    # ──────────────────────────────────────────

    async def _demo_cache(self) -> None:
        print("-- 角色一：缓存（cache-aside）--")
        calls = {"n": 0}

        def load_user() -> dict:
            """模拟昂贵的回源查询（如 RDB）。"""
            calls["n"] += 1
            time.sleep(0.3)
            return {"id": "42", "name": "shawn", "plan": "pro"}

        t0 = time.perf_counter()
        v1 = await self.cache.get_or_set("user:42", load_user, ttl_seconds=30)
        t1 = time.perf_counter() - t0

        t0 = time.perf_counter()
        v2 = await self.cache.get_or_set("user:42", load_user, ttl_seconds=30)
        t2 = time.perf_counter() - t0

        print(f"    第一次：回源 {t1 * 1000:.0f}ms  loader 调用 {calls['n']} 次 -> {v1}")
        print(f"    第二次：缓存 {t2 * 1000:.0f}ms  loader 调用 {calls['n']} 次 -> {v2}")
        print(f"    TTL 剩余 {await self.cache.ttl('user:42')}s")
        await self.cache.invalidate("user:42")
        print(f"    失效后再读：{await self.cache.get('user:42', default='<未命中，符合预期>')}\n")

    # ──────────────────────────────────────────
    # 角色四：限流器
    # ──────────────────────────────────────────

    async def _demo_rate_limiter(self) -> None:
        print("-- 角色四：限流器（ZSET 滑动窗口 + Lua）--")

        print("    单窗口规则：5 次/秒，连打 8 次")
        results = [await self.limiter.allow("api:u42", limit=5, window_ms=1000) for _ in range(8)]
        marks = "".join("[ok]" if ok else "[X]" for ok in results)
        print(f"    结果：{marks}")

        print("    多窗口规则：3 次/秒 且 5 次/2秒")
        rules = [WindowRule(window_ms=1000, limit=3), WindowRule(window_ms=2000, limit=5)]
        # 时间线：0s x3 -> 0.5s x2（第5次仍放行）-> 0.9s x1（1s窗口仍满，被拒）
        timeline = [0.0, 0.0, 0.0, 0.5, 0.5, 0.4, 2.0]
        line = []
        prev = 0.0
        for wait in timeline:
            await asyncio.sleep(wait - prev)
            prev = wait
            code = await self.limiter.allow_multi("api:u42", rules)
            line.append("ok" if code == 0 else f"W{code}")
        print(f"    时间线（间隔 {timeline}s）：{line}")
        print("    （W1/W2 = 被第 1/2 个窗口拦下，且两个窗口都不会写入该请求）\n")

    # ──────────────────────────────────────────
    # 角色三：延迟队列
    # ──────────────────────────────────────────

    async def _demo_delay_queue(self) -> None:
        print("-- 角色三：延迟队列（ZSET 时间轮 + BLPOP 唤醒 + reclaim）--")
        queue = "reminders"
        fail_count = {"n": 0}

        async def handler(task_id: str, payload: dict) -> None:
            action = payload.get("action")
            if action == "retry-me":
                fail_count["n"] += 1
                if fail_count["n"] < 2:
                    raise RuntimeError("模拟处理方第一次崩溃（任务留在 processing 等 reclaim）")
                elapsed = time.time() * 1000 - payload["_enqueued_at"]
                print(f"    [handler] reclaim 救回重试成功 task={task_id[:8]} 共尝试 {fail_count['n']} 次")
                return
            elapsed = time.time() * 1000 - payload["_enqueued_at"]
            print(f"    [handler] {time.strftime('%H:%M:%S')} 执行 task={task_id[:8]} "
                  f"action={action}（实际延迟 {elapsed:.0f}ms）")

        # reclaim 超时 3s、扫描间隔 1s：让失败任务几秒内被救回
        dispatcher = DelayDispatcher(
            queue, handler, reclaim_timeout_s=3.0, reclaim_interval_s=1.0
        )
        await dispatcher.start()

        print("    入队：charge@1s / email@2s / report@3s / retry-me@1.5s（第一次会失败）")
        await delay_queue.enqueue(queue, {"action": "charge"}, 1.0)
        await delay_queue.enqueue(queue, {"action": "email"}, 2.0)
        await delay_queue.enqueue(queue, {"action": "report"}, 3.0)
        await delay_queue.enqueue(queue, {"action": "retry-me"}, 1.5)

        # 观察至队列清空（retry-me 需要失败 + 3s 超时 + reclaim，约 6~8s）
        for _ in range(100):
            await asyncio.sleep(0.2)
            status = await delay_queue.get_queue_status(queue)
            if status["pending"] == 0 and status["processing"] == 0:
                break

        status = await delay_queue.get_queue_status(queue)
        print(f"    最终队列状态：{status}（应为全 0：处理完的任务都被 ACK）")
        await dispatcher.stop()


def main() -> None:
    asyncio.run(FourRolesDemo().run())


if __name__ == "__main__":
    main()
