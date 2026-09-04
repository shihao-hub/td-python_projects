"""角色三：延迟队列 —— 常驻 Dispatcher 协程。

对应 creativault 的 src/infrastructure/delay_queue/dispatcher.py，
唯一（也是关键的）改动：creativault 到期后投递 Kafka，教学版改为
调用构造时注入的 async handler —— 这就是"与业务解耦"的落点。

核心循环：
    while not shutdown:
        BLPOP wakeup（最多阻塞 blpop_timeout 秒）   ← 事件驱动：来任务立刻醒
        dequeue_expired(batch)                       ← Lua 原子取到期任务
        for task in tasks: handler(task) 成功 → ack   ← 手动 ACK
        每 reclaim_interval_s 扫一次 processing 超时  ← 崩溃兜底：reclaim 重入
        任何异常 → 日志 + sleep 1s + 继续              ← while(true) 自愈
"""

import asyncio
import time
from typing import Any, Awaitable, Callable

from apps.redis_four_roles.infra.delay_queue.manager import DelayQueueManager, delay_queue
from apps.redis_four_roles.infra.delay_queue.models import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_BLPOP_TIMEOUT_S,
    DEFAULT_DISPATCHER_STOP_TIMEOUT_S,
    DEFAULT_RECLAIM_INTERVAL_S,
    DEFAULT_RECLAIM_TIMEOUT_S,
    wakeup_key,
)

Handler = Callable[[str, dict[str, Any]], Awaitable[None]]
"""handler(task_id, payload)：处理成功正常返回即视为可 ACK；
抛异常则任务留在 processing，等 reclaim 超时重入。"""


class DelayDispatcher:
    """单队列常驻 Dispatcher。用法：

        dispatcher = DelayDispatcher("orders", handler=my_handler)
        await dispatcher.start()
        ...
        await dispatcher.stop()
    """

    def __init__(
        self,
        queue: str,
        handler: Handler,
        manager: DelayQueueManager | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        blpop_timeout: float = DEFAULT_BLPOP_TIMEOUT_S,
        reclaim_timeout_s: float = DEFAULT_RECLAIM_TIMEOUT_S,
        reclaim_interval_s: float = DEFAULT_RECLAIM_INTERVAL_S,
    ) -> None:
        self.queue = queue
        self.handler = handler
        self.dq = manager or delay_queue
        self.batch_size = batch_size
        self.blpop_timeout = blpop_timeout
        self.reclaim_timeout_s = reclaim_timeout_s
        self.reclaim_interval_s = reclaim_interval_s
        self._task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()
        self._last_reclaim = 0.0

    # ──────────────────────────────────────────
    # 生命周期
    # ──────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._shutdown.clear()
        self._task = asyncio.create_task(self._run_loop())
        print(f"[dq] dispatcher started queue={self.queue}")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._shutdown.set()
        try:
            await asyncio.wait_for(self._task, timeout=DEFAULT_DISPATCHER_STOP_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        print(f"[dq] dispatcher stopped queue={self.queue}")

    # ──────────────────────────────────────────
    # 核心循环
    # ──────────────────────────────────────────

    async def _run_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await self._blpop_wakeup()
                tasks = await self.dq.dequeue_expired(self.queue, batch_size=self.batch_size)
                for task_id, payload in tasks:
                    await self._handle(task_id, payload)
                await self._maybe_reclaim()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[dq] loop error queue={self.queue} error={exc!r}，1s 后恢复")
                await asyncio.sleep(1.0)

    async def _blpop_wakeup(self) -> None:
        """阻塞等待唤醒信号；超时也返回（兜底轮询，防信号丢失）。"""
        try:
            await asyncio.wait_for(
                self.dq._redis.blpop(wakeup_key(self.queue), timeout=self.blpop_timeout),
                timeout=self.blpop_timeout + 1.0,
            )
        except asyncio.TimeoutError:
            pass

    async def _handle(self, task_id: str, payload: dict[str, Any]) -> None:
        """执行 handler：成功 → ack；失败 → 留在 processing 等 reclaim。"""
        try:
            await self.handler(task_id, payload)
        except Exception as exc:
            print(
                f"[dq] handler failed queue={self.queue} task={task_id[:8]} "
                f"error={exc!r}（留在 processing，等 reclaim 重试）"
            )
            return
        await self.dq.ack(self.queue, task_id)
        print(f"[dq] ack queue={self.queue} task={task_id[:8]}")

    async def _maybe_reclaim(self) -> None:
        now = time.monotonic()
        if now - self._last_reclaim < self.reclaim_interval_s:
            return
        self._last_reclaim = now
        try:
            reclaimed = await self.dq.reclaim_timed_out(
                self.queue, timeout_seconds=self.reclaim_timeout_s
            )
            if reclaimed:
                print(f"[dq] reclaimed {reclaimed} task(s) queue={self.queue}")
        except Exception as exc:
            print(f"[dq] reclaim error queue={self.queue} error={exc!r}")


class DelayDispatcherRegistry:
    """多队列 Dispatcher 注册表：统一注册、统一启停（app lifespan 挂载用）。"""

    def __init__(self) -> None:
        self._dispatchers: dict[str, DelayDispatcher] = {}

    def register(self, queue: str, handler: Handler, **kwargs) -> DelayDispatcher:
        dispatcher = DelayDispatcher(queue, handler, **kwargs)
        self._dispatchers[queue] = dispatcher
        return dispatcher

    async def start_all(self) -> None:
        for dispatcher in self._dispatchers.values():
            await dispatcher.start()

    async def stop_all(self) -> None:
        for dispatcher in self._dispatchers.values():
            await dispatcher.stop()

    @property
    def running_queues(self) -> list[str]:
        return [
            q
            for q, d in self._dispatchers.items()
            if d._task is not None and not d._task.done()
        ]


delay_dispatcher_registry = DelayDispatcherRegistry()
