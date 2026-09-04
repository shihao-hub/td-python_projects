"""角色四：限流器 —— ZSET 滑动窗口 + Lua 原子执行。

对应 creativault 的 src/infrastructure/redis.py 中的
sliding_window_allow / sliding_window_allow_multi（Lua 脚本原文照搬）。

为什么用 ZSET：
- member = 每次请求的唯一标识，score = 请求时间戳(ms)
- ZREMRANGEBYSCORE 清掉窗口外的旧请求 → ZCARD 数当前窗口内的请求数
- 整个"清理 → 计数 → 判断 → 写入"必须原子执行，否则并发下会多放行
  （先数后写的间隙里别的请求也数到旧值），所以整体放进一个 Lua 脚本

多窗口版（如同时限 QPS + 每分钟）采用"先全检查、全部通过才全写入"，
避免第一个窗口已写入、第二个窗口拒绝导致的计数污染。
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from apps.redis_four_roles.infra.redis_manager import RedisManager, redis_manager

# ─────────────────────────────────────────────
# 单窗口滑动限流 Lua（creativault 原版）
# KEYS[1] = ZSET key
# ARGV    = now_ms, window_ms, limit, member, ttl_ms
# 返回 0=放行（已记录） 1=超限（未记录）
# ─────────────────────────────────────────────
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local ttl = tonumber(ARGV[5])

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    return 1
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, ttl)
return 0
"""

# ─────────────────────────────────────────────
# 多窗口滑动限流 Lua（creativault 原版）
# KEYS[1..N] = 各窗口 ZSET key
# ARGV[1..3] = now_ms, member, cost
# ARGV[4..]  = 每窗口一对 (window_ms, limit)
# 返回 0=全部通过（已写入） i=第 i 个窗口超限（未写入任何窗口）
# ─────────────────────────────────────────────
_SLIDING_WINDOW_MULTI_LUA = """
local now = tonumber(ARGV[1])
local member = ARGV[2]
local cost = math.max(1, tonumber(ARGV[3]) or 1)
local n = #KEYS

for i = 1, n do
    local key = KEYS[i]
    local window = tonumber(ARGV[3 + (i - 1) * 2 + 1])
    local limit = tonumber(ARGV[3 + (i - 1) * 2 + 2])
    redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
    local count = redis.call('ZCARD', key)
    if count + cost > limit then
        return i
    end
end

for i = 1, n do
    local key = KEYS[i]
    local window = tonumber(ARGV[3 + (i - 1) * 2 + 1])
    for slot = 1, cost do
        redis.call('ZADD', key, now, member .. ':' .. slot)
    end
    redis.call('PEXPIRE', key, window)
end
return 0
"""


@dataclass
class WindowRule:
    """一个限流窗口规则：window_ms 内最多 limit 次。"""

    window_ms: int
    limit: int

    @property
    def label(self) -> str:
        return f"{self.limit}/{self.window_ms}ms"


class SlidingWindowRateLimiter:
    """滑动窗口限流器（协议无关、业务无关，key 由调用方拼装维度）。

    失败语义与 creativault 一致：Redis/Lua 异常直接抛出，
    由调用方决定降级策略（HTTP 场景通常"故障放行"，计费场景应拒绝）。
    """

    def __init__(self, manager: Optional[RedisManager] = None) -> None:
        self._manager = manager or redis_manager

    @property
    def _redis(self):
        return self._manager.ensure_initialized().redis

    @staticmethod
    def _now_ms() -> int:
        return int(datetime.now(timezone.utc).timestamp() * 1000)

    @staticmethod
    def _member(now_ms: int) -> str:
        # member 唯一化：同毫秒的并发请求不会互相覆盖
        return f"{now_ms}-{uuid.uuid4().hex[:8]}"

    async def allow(self, key: str, limit: int, window_ms: int = 1000) -> bool:
        """单窗口限流：True=放行 False=超限。"""
        now_ms = self._now_ms()
        result = await self._redis.eval(
            _SLIDING_WINDOW_LUA,
            1,
            f"lab:rate:{key}",
            str(now_ms),
            str(window_ms),
            str(limit),
            self._member(now_ms),
            str(window_ms),
        )
        return int(result) == 0

    async def allow_multi(
        self,
        key: str,
        rules: Sequence[WindowRule],
        cost: int = 1,
    ) -> int:
        """多窗口限流：同时校验多个窗口。

        Returns:
            0 表示全部通过；i (>0) 表示第 i 个窗口（1-based）超限。
        """
        if not rules:
            return 0
        now_ms = self._now_ms()
        member = self._member(now_ms)

        keys = [f"lab:rate:{key}:w{i}" for i in range(len(rules))]
        argv: list[str] = [str(now_ms), member, str(max(1, cost))]
        for rule in rules:
            argv.append(str(rule.window_ms))
            argv.append(str(rule.limit))

        result = await self._redis.eval(
            _SLIDING_WINDOW_MULTI_LUA,
            len(keys),
            *keys,
            *argv,
        )
        return int(result)
