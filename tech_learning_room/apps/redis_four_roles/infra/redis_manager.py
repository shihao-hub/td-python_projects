"""Redis 连接池管理器 —— 四种角色共用的唯一入口。

对应 creativault 的 src/infrastructure/redis.py::RedisManager（去掉了
celery prefork 场景下的 event loop 漂移检测，教学版不需要）。

要点：
- redis.asyncio 的连接池绑定创建它的 event loop；一个 demo 进程一个池即可
- 懒初始化 + ensure_initialized：第一次用时才建池，失败可重试
"""

import asyncio
from typing import Optional

from redis.asyncio import BlockingConnectionPool, Redis

from apps.redis_four_roles.config import settings


class RedisManager:
    """Redis 连接池管理器：init → use → close。"""

    def __init__(self) -> None:
        self._pool: Optional[BlockingConnectionPool] = None
        self._redis: Optional[Redis] = None

    def init_pool(self, redis_url: Optional[str] = None) -> None:
        """初始化连接池（幂等：重复调用会重建）。"""
        url = redis_url or settings.redis_url
        self._pool = BlockingConnectionPool.from_url(
            url,
            max_connections=20,
            timeout=5,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
            health_check_interval=30,
        )
        self._redis = Redis(connection_pool=self._pool)

    def ensure_initialized(self) -> "RedisManager":
        """确保已初始化（懒初始化入口）。"""
        if self._redis is None:
            self.init_pool()
        return self

    @property
    def redis(self) -> Redis:
        """获取 Redis 客户端实例。"""
        if self._redis is None:
            raise RuntimeError("Redis 未初始化，请先调用 init_pool()")
        return self._redis

    async def ping(self) -> bool:
        """健康检查。"""
        try:
            return bool(await self.redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        """关闭连接。"""
        if self._redis:
            await self._redis.aclose()
        if self._pool:
            await self._pool.disconnect()
        self._redis = None
        self._pool = None


# 全局单例：四种角色共享
redis_manager = RedisManager()


def run(coro) -> None:
    """demo 辅助：统一 event loop 入口。"""
    asyncio.run(coro)
