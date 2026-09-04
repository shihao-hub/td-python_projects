"""角色一：缓存（cache-aside 模式）。

对应 creativault 的 src/infrastructure/redis.py::RedisCache。
核心思想：
- 读：先 GET，未命中再查源（loader），回写 SET+TTL
- 写/删：更新源后主动删缓存（而不是更新缓存），避免并发写脏数据
- 一切失败都降级为"直接查源"：缓存挂了不能拖垮业务

序列化策略与 creativault 相同：值统一 JSON 序列化成字符串存 String。
"""

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Optional

from apps.redis_four_roles.config import settings
from apps.redis_four_roles.infra.redis_manager import RedisManager, redis_manager


class CacheManager:
    """带 TTL 与 cache-aside 语义的字符串缓存封装。"""

    def __init__(self, manager: Optional[RedisManager] = None, prefix: str = "lab:cache") -> None:
        self._manager = manager or redis_manager
        self._prefix = prefix

    # ──────────────────────────────────────────
    # key 命名：所有角色、所有业务用前缀隔离
    # ──────────────────────────────────────────

    def _k(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    @property
    def _redis(self):
        return self._manager.ensure_initialized().redis

    # ──────────────────────────────────────────
    # 基础读写
    # ──────────────────────────────────────────

    async def get(self, key: str, default: Any = None) -> Any:
        """读缓存；键不存在或 Redis 故障时返回 default（降级不抛异常）。"""
        try:
            raw = await self._redis.get(self._k(key))
            if raw is None:
                return default
            return json.loads(raw)
        except Exception:
            return default

    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> bool:
        """写缓存，默认带 TTL（防止冷 key 永不过期）。"""
        ttl = ttl_seconds if ttl_seconds is not None else settings.cache_default_ttl
        try:
            await self._redis.set(self._k(key), json.dumps(value, ensure_ascii=False), ex=ttl)
            return True
        except Exception:
            return False

    async def delete(self, *keys: str) -> int:
        """删除缓存（cache-aside 的"写后失效"就用它）。"""
        try:
            return int(await self._redis.delete(*(self._k(k) for k in keys)))
        except Exception:
            return 0

    async def ttl(self, key: str) -> int:
        """查看剩余 TTL（秒）；-2 表示键不存在。"""
        try:
            return int(await self._redis.ttl(self._k(key)))
        except Exception:
            return -2

    # ──────────────────────────────────────────
    # cache-aside 核心：get_or_set
    # ──────────────────────────────────────────

    async def get_or_set(
        self,
        key: str,
        loader: Callable[[], Any] | Callable[[], Awaitable[Any]],
        ttl_seconds: Optional[int] = None,
    ) -> Any:
        """cache-aside 读路径：未命中则执行 loader 回源并回写。

        loader 可为同步或异步函数。任何 Redis 异常都降级为直接回源
        （对应 creativault RedisCache.get_or_set 的失败语义）。
        """
        cached = await self.get(key, default=_MISS)
        if cached is not _MISS:
            self._log("hit", key)
            return cached

        self._log("miss", key)
        if asyncio.iscoroutinefunction(loader):
            fresh = await loader()
        else:
            fresh = loader()
        await self.set(key, fresh, ttl_seconds)
        return fresh

    async def invalidate(self, key: str) -> None:
        """写路径的失效：源数据更新后调用。"""
        await self.delete(key)

    def _log(self, event: str, key: str) -> None:
        print(f"[cache] {event:4s} key={self._k(key)} ts={time.strftime('%H:%M:%S')}")


class _Miss:
    """哨兵：区分"缓存了 None"和"未命中"。"""


_MISS = _Miss()
