"""五个场景：缓存击穿 / 穿透 / 雪崩 / 失效竞态 / 分布式锁。"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid

import redis.asyncio as aioredis

from apps.common.lab import Timer, banner, check, conclude, fact, step
from apps.common.settings import settings

PREFIX = "tlr_cc"

# 模拟的"数据库"：loader 调用次数 = DB 压力
_FAKE_DB: dict[int, dict | None] = {}
_LOADER_CALLS = 0


def _reset_db(rows: dict[int, dict | None]) -> None:
    global _LOADER_CALLS
    _FAKE_DB.clear()
    _FAKE_DB.update(rows)
    _LOADER_CALLS = 0


async def _loader(user_id: int, latency: float = 0.3) -> dict | None:
    """模拟昂贵的回源查询：300ms、计数。返回 None 表示该行不存在。"""
    global _LOADER_CALLS
    _LOADER_CALLS += 1
    await asyncio.sleep(latency)
    return _FAKE_DB.get(user_id)


def _k(key: str) -> str:
    return f"{PREFIX}:{key}"


async def _flush(client: aioredis.Redis) -> None:
    keys = await client.keys(f"{PREFIX}:*")
    if keys:
        await client.delete(*keys)


# ──────────────────────────────────────────────────────────────────────────
# 场景 1：缓存击穿 —— 热点 key 过期瞬间的并发回源，与合并回源（mutex）
# ──────────────────────────────────────────────────────────────────────────

async def _naive_get_or_set(client: aioredis.Redis, user_id: int) -> dict | None:
    """朴素 cache-aside：miss 后各自回源（击穿现场）。"""
    raw = await client.get(_k(f"u:{user_id}"))
    if raw is not None:
        return json.loads(raw)
    value = await _loader(user_id)
    if value is not None:
        await client.set(_k(f"u:{user_id}"), json.dumps(value), ex=60)
    return value


async def _mutex_get_or_set(client: aioredis.Redis, user_id: int) -> dict | None:
    """合并回源：SET NX 抢锁者回源，其余人短暂等待后重读缓存。"""
    key = _k(f"u:{user_id}")
    raw = await client.get(key)
    if raw is not None:
        return json.loads(raw)

    lock_key = _k(f"lock:u:{user_id}")
    got = await client.set(lock_key, "1", nx=True, ex=5)
    if got:
        try:
            raw = await client.get(key)  # 双检查：等锁期间可能已被回填
            if raw is not None:
                return json.loads(raw)
            value = await _loader(user_id)
            if value is not None:
                await client.set(key, json.dumps(value), ex=60)
            return value
        finally:
            await client.delete(lock_key)

    # 未抢到锁：等待回填后重读（上限 5s）
    for _ in range(100):
        await asyncio.sleep(0.05)
        raw = await client.get(key)
        if raw is not None:
            return json.loads(raw)
    return await _loader(user_id)  # 兜底：锁持有者崩溃


async def scenario_stampede() -> None:
    banner("场景 1：缓存击穿 —— 热点 key 失效瞬间，20 个并发请求同时打向 DB")
    client = aioredis.from_url(settings.redis_url)
    try:
        _reset_db({42: {"id": 42, "name": "shawn", "plan": "pro"}})

        step("阶段 A：朴素 cache-aside（miss 后各自回源）")
        await _flush(client)
        with Timer() as t:
            values = await asyncio.gather(*[_naive_get_or_set(client, 42) for _ in range(20)])
        fact("DB 回源次数", _LOADER_CALLS)
        fact("总耗时", f"{t.ms:.0f}ms")
        check(_LOADER_CALLS == 20 and all(v is not None for v in values), "20 个请求全部打到 DB：瞬时压力被放大 20 倍", "未复现")

        step("阶段 B：合并回源（SET NX 抢锁 + 双检查）")
        _reset_db({42: {"id": 42, "name": "shawn", "plan": "pro"}})
        await _flush(client)
        with Timer() as t:
            values = await asyncio.gather(*[_mutex_get_or_set(client, 42) for _ in range(20)])
        fact("DB 回源次数", _LOADER_CALLS)
        fact("总耗时", f"{t.ms:.0f}ms")
        check(_LOADER_CALLS == 1 and all(v is not None for v in values), "仅 1 次回源，其余 19 个请求等待后读缓存", "合并回源失败")
        conclude(
            "击穿的解法不是'不失效'，而是把回源收敛为单飞：锁只保护'回源+回填'这一小段，"
            "等待者很快能读到新缓存。若热点 key 极度集中，可再叠加'逻辑过期'（值内嵌过期时间，"
            "过期后返回旧值并异步刷新），彻底消灭 miss 路径上的阻塞。"
        )
    finally:
        await client.aclose()


# ──────────────────────────────────────────────────────────────────────────
# 场景 2：缓存穿透 —— 查询不存在的数据，与空值缓存
# ──────────────────────────────────────────────────────────────────────────

async def scenario_penetration() -> None:
    banner("场景 2：缓存穿透 —— 恶意/误传的不存在 id，每次都绕过缓存直击 DB")
    client = aioredis.from_url(settings.redis_url)
    try:
        _reset_db({})  # id=999 不存在

        step("阶段 A：不存在的 id=999 反复查询（朴素逻辑：None 不写缓存）")
        await _flush(client)
        for _ in range(10):
            raw = await client.get(_k("u:999"))
            if raw is None:
                await _loader(999)
        fact("DB 查询次数", _LOADER_CALLS)
        check(_LOADER_CALLS == 10, "10 次请求 10 次 DB：穿透放大", "未复现")

        step("阶段 B：空值缓存（None 也写缓存，但 TTL 很短）")
        _reset_db({})
        await _flush(client)
        for _ in range(10):
            raw = await client.get(_k("u:999"))
            if raw is None:
                value = await _loader(999)
                # 空值用哨兵字符串标记；TTL 必须短，避免"数据新建后缓存还挡着"
                await client.set(_k("u:999"), json.dumps(value) if value else "<nil>", ex=10)
        fact("DB 查询次数", _LOADER_CALLS)
        fact("TTL 剩余", await client.ttl(_k("u:999")))
        check(_LOADER_CALLS == 1, "仅第一次打到 DB，后续被空值缓存挡住", "空值缓存未生效")
        conclude(
            "空值缓存是拿少量内存换 DB 保护：TTL 要短（秒级），且写入侧新建数据后主动删除对应空值 key。"
            "海量随机穿透（扫描攻击）则用布隆过滤器在缓存前再做一层存在性预判。"
        )
    finally:
        await client.aclose()


# ──────────────────────────────────────────────────────────────────────────
# 场景 3：缓存雪崩 —— 同刻批量过期，与 TTL 抖动
# ──────────────────────────────────────────────────────────────────────────

async def scenario_ttl_jitter() -> None:
    banner("场景 3：缓存雪崩 —— 100 个 key 同时过期 vs TTL 随机抖动")
    client = aioredis.from_url(settings.redis_url)
    try:
        base = int(time.time()) + 300  # 基础过期时刻

        await _flush(client)
        for i in range(100):  # 固定 TTL：全部在同一秒过期
            await client.set(_k(f"fixed:{i}"), str(i), exat=base)
        for i in range(100):  # 抖动 TTL：±60s 内随机摊开
            jitter = random.randint(-60, 60)
            await client.set(_k(f"jitter:{i}"), str(i), exat=base + jitter)

        async def spread(prefix: str) -> tuple[int, int]:
            keys = await client.keys(_k(f"{prefix}:*"))
            times = await asyncio.gather(*[client.expiretime(k) for k in keys])
            uniq = len(set(times))
            return uniq, len(times)

        fixed_uniq, fixed_total = await spread("fixed")
        jitter_uniq, jitter_total = await spread("jitter")
        fact("固定 TTL", f"{fixed_total} 个 key 仅分布在 {fixed_uniq} 个过期时刻（同一秒集体失效）")
        fact("抖动 TTL", f"{jitter_total} 个 key 分布在 {jitter_uniq} 个过期时刻（摊开成 2 分钟）")
        check(fixed_uniq <= 2 and jitter_uniq > 60, "抖动把过期压力摊平到时间轴上", "抖动未生效")
        conclude(
            "雪崩三因素：同刻过期 / Redis 宕机 / 热点大户。TTL 抖动治第一类；"
            "多副本与熔断降级治第二类。批量预热的批处理任务尤其要加抖动，"
            "否则'整批写入'意味着'整批同时失效'。"
        )
    finally:
        await client.aclose()


# ──────────────────────────────────────────────────────────────────────────
# 场景 4：失效竞态 —— 先更新 DB 再删缓存，仍会脏的窗口，与延迟双删
# ──────────────────────────────────────────────────────────────────────────

async def scenario_invalidate_race() -> None:
    banner("场景 4：失效竞态 —— 读线程的'慢回填'把旧值写回了缓存")
    client = aioredis.from_url(settings.redis_url)
    try:
        db = {"balance": 100}

        async def read_db() -> int:
            await asyncio.sleep(0.2)  # 模拟一次普通查询耗时
            return db["balance"]

        step("阶段 A：裸奔的 cache-aside")
        await _flush(client)
        # 读线程 A：miss -> 从 DB 查到旧值 100（此刻还没回填缓存）
        v_a = await read_db()
        step(f"  读 A：cache miss，从 DB 读到 {v_a}（尚未回填）")
        # 写线程 B：更新 DB + 删缓存（此刻缓存本来就是空的，删了个寂寞）
        db["balance"] = 200
        await client.delete(_k("balance"))
        step("  写 B：DB 更新为 200，DEL 缓存（此时缓存无 key，删除空转）")
        # A 这才把旧值回填
        await client.set(_k("balance"), json.dumps(v_a), ex=60)
        step(f"  读 A：回填缓存 = {v_a}   <-- 旧值覆盖了新事实")
        cached = json.loads(await client.get(_k("balance")))
        fact("缓存值 / DB 值", f"{cached} / {db['balance']}")
        check(cached == 100 and db["balance"] == 200, "脏缓存已形成：在 TTL 内所有读都拿到旧值", "未复现")

        step("阶段 B：延迟双删 —— 写侧在短暂等待后再删一次，清掉 A 的迟到回填")
        db["balance"] = 100
        # 重演同一时序
        v_a = await read_db()
        db["balance"] = 200
        await client.delete(_k("balance"))  # 第一次删
        await client.set(_k("balance"), json.dumps(v_a), ex=60)  # A 迟到回填
        await asyncio.sleep(0.3)  # 关键：等"在途读"完成回填
        await client.delete(_k("balance"))  # 第二次删：清掉脏值
        cached = await client.get(_k("balance"))
        fact("第二次删除后缓存值", cached)
        check(cached is None, "迟到回填被第二次删除清掉，下一次读将拿到 200", "双删未生效")
        conclude(
            "先更新后删除（cache-aside 标准写法）在'A 读慢 + B 写快'的窗口里仍会脏；"
            "延迟双删是工程上的补丁——不完美（延迟多长？删失败怎么办？），"
            "更强的方案是让写更新带版本号的 CAS 回填、或订阅 binlog 的订阅式失效。"
            "没有零窗口方案，只有'窗口多小 + 脏多久'的取舍。"
        )
    finally:
        await client.aclose()


# ──────────────────────────────────────────────────────────────────────────
# 场景 5：分布式锁 —— 无 token 误删、Lua 比较删除，与租约的本质
# ──────────────────────────────────────────────────────────────────────────

# Lua 比较删除：GET==token 才 DEL，读与删在一个脚本内原子完成
_COMPARE_DEL_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


async def scenario_distributed_lock() -> None:
    banner("场景 5：分布式锁 —— 误删他人锁、所有权 token，与'租约不是排他'")
    client = aioredis.from_url(settings.redis_url)
    try:
        lock_key = _k("lock:job")

        step("阶段 A：无 token 的锁 -> 业务超时后误删他人的锁")
        await _flush(client)
        await client.set(lock_key, "holder-A", nx=True, ex=1)  # A 拿锁，TTL 1s
        await asyncio.sleep(1.2)  # A 的业务超过了租约，锁已自动过期
        await client.set(lock_key, "holder-B", nx=True, ex=5)  # B 依法获得锁
        step("  A 超时锁过期 -> B 获得新锁")
        await client.delete(lock_key)  # A 完成后无脑 DEL
        b_still_locked = await client.get(lock_key)
        fact("A 执行 DEL 后 B 的锁", b_still_locked)
        check(b_still_locked is None, "A 把 B 的锁删了！下一个申请者 C 将与 B 并发执行", "未复现")

        step("阶段 B：随机 token + Lua 比较删除")
        await _flush(client)
        token_a = uuid.uuid4().hex
        await client.set(lock_key, token_a, nx=True, ex=1)  # A 持 token_a
        await asyncio.sleep(1.2)  # 过期
        token_b = uuid.uuid4().hex
        await client.set(lock_key, token_b, nx=True, ex=5)  # B 持 token_b
        deleted = await client.eval(_COMPARE_DEL_LUA, 1, lock_key, token_a)
        fact("A 用 token_a 尝试 Lua 比较删除的返回", f"{deleted}（0=未删：锁已不属于 A）")
        b_still_locked = await client.get(lock_key)
        check(deleted == 0 and b_still_locked == token_b, "B 的锁安然无恙：只有持有者能释放", "token 校验未生效")
        conclude(
            "锁的三层认知：①加锁用 SET NX EX 一步完成（SETNX + EXPIRE 两步会在客户端崩溃时留下死锁）；"
            "②释放必须校验所有权（随机 token + Lua 原子比较删除）；"
            "③最根本的：锁是租约不是排他保证——A 超时后仍会继续执行自己的临界区，"
            "若业务不可容忍并发执行，需要 fencing token（递增版本号）由资源方做最终仲裁，"
            "或直接依赖数据库约束（回到 postgres_transactions 的思路）。"
        )
    finally:
        await client.aclose()


# ──────────────────────────────────────────────────────────────────────────
# 场景注册表
# ──────────────────────────────────────────────────────────────────────────

SCENARIOS: dict[str, object] = {}
DESCRIPTIONS: dict[str, str] = {}


def _reg(name: str, fn, desc: str) -> None:
    SCENARIOS[name] = fn
    DESCRIPTIONS[name] = desc


_reg("stampede", scenario_stampede, "击穿：并发回源 vs SET NX 合并回源（单飞）")
_reg("penetration", scenario_penetration, "穿透：不存在的 id vs 空值缓存")
_reg("ttl-jitter", scenario_ttl_jitter, "雪崩：同刻过期 vs TTL 随机抖动")
_reg("invalidate-race", scenario_invalidate_race, "失效竞态：迟到回填脏缓存 vs 延迟双删")
_reg("distributed-lock", scenario_distributed_lock, "分布式锁：误删 / token+Lua / 租约本质")


async def run_all() -> None:
    for fn in SCENARIOS.values():
        await fn()
