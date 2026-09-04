"""角色三：延迟队列 —— Redis 操作管理器。

对应 creativault 的 src/infrastructure/delay_queue/manager.py（原版）。
封装 enqueue / dequeue_expired / ack / reclaim_timed_out 四个核心操作，
全部走 Lua 保证多 Worker 安全。
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from apps.redis_four_roles.infra.delay_queue import lua_scripts
from apps.redis_four_roles.infra.delay_queue.models import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_RECLAIM_TIMEOUT_S,
    pending_key,
    processing_key,
    tasks_key,
    wakeup_key,
)
from apps.redis_four_roles.infra.redis_manager import RedisManager, redis_manager


class DelayQueueManager:
    """延时队列 Redis 操作封装（async，多实例并发安全）。"""

    def __init__(self, manager: RedisManager | None = None) -> None:
        self._manager = manager or redis_manager

    @property
    def _redis(self):
        return self._manager.ensure_initialized().redis

    # ──────────────────────────────────────────
    # enqueue — 入队
    # ──────────────────────────────────────────

    async def enqueue(self, queue: str, payload: dict[str, Any], delay_seconds: float) -> str:
        """把任务加入延时队列，返回 task_id。

        存储动作（一次 Lua 完成）：
        1. ZADD pending，score = 到期时间戳 → ZSET 变成按到期时间排序的"时间轮"
        2. HSET tasks 载荷 JSON（ZSET 的 member 只有 id，正文放 HASH）
        3. LPUSH wakeup 唤醒信号 → 正在 BLPOP 的 Dispatcher 立刻醒来检查
        """
        task_id = uuid.uuid4().hex
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        expire_ms = now_ms + int(delay_seconds * 1000)

        stored_payload = {
            **payload,
            "_task_id": task_id,
            "_queue": queue,
            "_enqueued_at": now_ms,
            "_expire_at": expire_ms,
        }
        payload_json = json.dumps(stored_payload, ensure_ascii=False, default=str)

        await self._redis.eval(
            lua_scripts.ENQUEUE_LUA,
            3,
            pending_key(queue),
            tasks_key(queue),
            wakeup_key(queue),
            task_id,
            str(expire_ms),
            payload_json,
        )
        print(
            f"[dq] enqueue queue={queue} task={task_id[:8]} "
            f"delay={delay_seconds}s expire_at={expire_ms}"
        )
        return task_id

    # ──────────────────────────────────────────
    # dequeue_expired — 原子取出到期任务
    # ──────────────────────────────────────────

    async def dequeue_expired(
        self, queue: str, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> list[tuple[str, dict[str, Any]]]:
        """取出已到期任务 pending → processing，返回 [(task_id, payload)]。

        同一 task_id 只会被一个调用方取到（Lua 原子性）。
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        result = await self._redis.eval(
            lua_scripts.DEQUEUE_LUA,
            3,
            pending_key(queue),
            tasks_key(queue),
            processing_key(queue),
            str(now_ms),
            str(batch_size),
        )
        if not result:
            return []

        tasks: list[tuple[str, dict[str, Any]]] = []
        for i in range(0, len(result), 2):
            task_id, payload_json = result[i], result[i + 1]
            try:
                payload = json.loads(payload_json) if payload_json else {}
            except (json.JSONDecodeError, TypeError):
                # 单条损坏不阻塞整批：清掉，跳过
                await self._redis.zrem(processing_key(queue), task_id)
                await self._redis.hdel(tasks_key(queue), task_id)
                continue
            tasks.append((task_id, payload))
        return tasks

    # ──────────────────────────────────────────
    # ack — 业务处理成功后确认
    # ──────────────────────────────────────────

    async def ack(self, queue: str, task_id: str) -> bool:
        """确认完成：从 processing 与 tasks 双清。不命中返回 False。"""
        removed = await self._redis.eval(
            lua_scripts.ACK_LUA,
            2,
            processing_key(queue),
            tasks_key(queue),
            task_id,
        )
        return int(removed) > 0

    # ──────────────────────────────────────────
    # reclaim — 处理方崩溃/超时的兜底重入
    # ──────────────────────────────────────────

    async def reclaim_timed_out(
        self,
        queue: str,
        timeout_seconds: float = DEFAULT_RECLAIM_TIMEOUT_S,
        batch_size: int = 100,
    ) -> int:
        """把 processing 中超时未 ACK 的任务放回 pending（score=0 立即重试）。"""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        cutoff_ms = now_ms - int(timeout_seconds * 1000)
        reclaimed = await self._redis.eval(
            lua_scripts.RECLAIM_LUA,
            3,
            processing_key(queue),
            pending_key(queue),
            wakeup_key(queue),
            str(cutoff_ms),
            str(batch_size),
        )
        return int(reclaimed)

    # ──────────────────────────────────────────
    # 可观测性
    # ──────────────────────────────────────────

    async def get_queue_status(self, queue: str) -> dict[str, int]:
        """队列状态：pending / processing / tasks 计数。"""
        redis = self._redis
        return {
            "pending": int(await redis.zcard(pending_key(queue))),
            "processing": int(await redis.zcard(processing_key(queue))),
            "tasks": int(await redis.hlen(tasks_key(queue))),
        }


# 全局单例（与 creativault 一致：整个进程共享一个 manager）
delay_queue = DelayQueueManager()
