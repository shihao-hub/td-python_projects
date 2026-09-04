"""redis_four_roles — 一个 Redis 同时扮演四种角色的教学实现。

角色清单（对照 creativault 线上实现，但零业务耦合）：

1. 缓存         infra/cache.py            CacheManager（cache-aside / TTL / 失效）
2. Celery 通道  celery_lab/app.py         broker + result backend 都指向同一个 Redis
3. 延迟队列     infra/delay_queue/        ZSET 时间轮 + HASH 载荷 + LIST 唤醒 + Lua 原子脚本
4. 限流器       infra/rate_limiter.py     ZSET 滑动窗口（单窗口 / 多窗口）+ Lua 原子执行

所有角色共享同一个 RedisManager 连接池 —— 这正是"一个 Redis 多角色"的含义：
不同的 key 前缀 + 不同的数据结构 + 不同的访问模式，物理上是同一个实例。
"""
