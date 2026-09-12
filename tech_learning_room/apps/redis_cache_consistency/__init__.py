"""redis_cache_consistency —— 缓存一致性的五个经典现场（redis.asyncio）。

统一约定：源（DB）用内存 dict 模拟，loader 的调用次数即"打到 DB 的压力"，
所有实验关注的是「缓存层的行为」，与具体 DB 无关。
"""
