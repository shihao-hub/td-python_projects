"""角色三：延迟队列 —— 四个 Lua 原子脚本（creativault 原版）。

为什么每个操作都要 Lua：
- enqueue 要同时动 3 个 key（ZADD + HSET + LPUSH 唤醒）
- dequeue 要"取一批到期任务并从 pending 搬进 processing"，多 Worker 并发
  下若不原子，同一个 task_id 会被两个 Worker 各取一次
- ack / reclaim 同理：跨两个 key 的状态迁移必须一次完成
"""

# ─────────────────────────────────────────────
# ENQUEUE — 原子入队
# KEYS[1] = pending ZSET / KEYS[2] = tasks HASH / KEYS[3] = wakeup LIST
# ARGV[1] = task_id, ARGV[2] = expire_ts(ms), ARGV[3] = payload_json
# ─────────────────────────────────────────────
ENQUEUE_LUA = """
local pending = KEYS[1]
local tasks = KEYS[2]
local wakeup = KEYS[3]
local task_id = ARGV[1]
local expire_ts = tonumber(ARGV[2])
local payload = ARGV[3]

redis.call('ZADD', pending, expire_ts, task_id)
redis.call('HSET', tasks, task_id, payload)
redis.call('LPUSH', wakeup, '1')
return 1
"""

# ─────────────────────────────────────────────
# DEQUEUE — 原子批量出队（pending → processing）
# KEYS[1] = pending ZSET / KEYS[2] = tasks HASH / KEYS[3] = processing ZSET
# ARGV[1] = now_ts(ms), ARGV[2] = max_count
# 返回 flat array: [task_id, payload_json, task_id, payload_json, ...]
# ─────────────────────────────────────────────
DEQUEUE_LUA = """
local pending = KEYS[1]
local tasks = KEYS[2]
local processing = KEYS[3]
local now = tonumber(ARGV[1])
local max_count = tonumber(ARGV[2])

local expired = redis.call('ZRANGEBYSCORE', pending, 0, now, 'LIMIT', 0, max_count)
local results = {}

for i = 1, #expired do
    local task_id = expired[i]
    redis.call('ZREM', pending, task_id)
    redis.call('ZADD', processing, now, task_id)
    local payload = redis.call('HGET', tasks, task_id)
    table.insert(results, task_id)
    table.insert(results, payload)
end

return results
"""

# ─────────────────────────────────────────────
# ACK — 确认完成（processing + tasks 双清）
# KEYS[1] = processing ZSET / KEYS[2] = tasks HASH
# ARGV[1] = task_id
# 返回 removed count（0 = 不在 processing：已 ACK 或已被 reclaim）
# ─────────────────────────────────────────────
ACK_LUA = """
local processing = KEYS[1]
local tasks = KEYS[2]
local task_id = ARGV[1]

local removed = redis.call('ZREM', processing, task_id)
if removed > 0 then
    redis.call('HDEL', tasks, task_id)
end
return removed
"""

# ─────────────────────────────────────────────
# RECLAIM — 超时重入（processing → pending，score=0 立即到期）
# KEYS[1] = processing ZSET / KEYS[2] = pending ZSET / KEYS[3] = wakeup LIST
# ARGV[1] = cutoff_ts(ms)，score < cutoff 视为超时；ARGV[2] = max_count
# ─────────────────────────────────────────────
RECLAIM_LUA = """
local processing = KEYS[1]
local pending = KEYS[2]
local wakeup = KEYS[3]
local cutoff = tonumber(ARGV[1])
local max_count = tonumber(ARGV[2])

local timed_out = redis.call('ZRANGEBYSCORE', processing, 0, cutoff, 'LIMIT', 0, max_count)
local reclaimed = 0

for i = 1, #timed_out do
    local task_id = timed_out[i]
    redis.call('ZREM', processing, task_id)
    redis.call('ZADD', pending, 0, task_id)
    reclaimed = reclaimed + 1
end

if reclaimed > 0 then
    redis.call('LPUSH', wakeup, '1')
end

return reclaimed
"""
