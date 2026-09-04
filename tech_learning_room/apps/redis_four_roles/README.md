# redis_four_roles — 一个 Redis 的四种角色

对照 creativault 线上实现的教学复刻，零业务耦合：把 creati-vault 里
Redis 同时承担的四种角色抽成独立、可运行的面向对象代码。

## 四种角色与 creativault 的对照

| 角色 | 本 app | creativault 出处 | Redis 数据结构 |
|---|---|---|---|
| 缓存 | `infra/cache.py` | `src/infrastructure/redis.py::RedisCache` | STRING + TTL |
| Celery 消息通道 | `celery_lab/app.py` | `src/worker/celery_app.py` | LIST（broker）+ STRING（backend） |
| 延迟队列 | `infra/delay_queue/` | `src/infrastructure/delay_queue/` | ZSET 时间轮 + HASH 载荷 + LIST 唤醒 |
| 限流器 | `infra/rate_limiter.py` | `src/infrastructure/redis.py::sliding_window_*` | ZSET 滑动窗口 + Lua |

"一个 Redis 多角色"的本质：**物理上同一个实例，靠 key 前缀 + 数据结构 +
访问模式划分出互不干扰的逻辑通道**。

## 运行

```bash
cd tech_learning_room
uv sync                       # 安装 redis + celery[redis]
cp .env.example .env          # 按需改 REDIS_URL
uv run python -m apps.redis_four_roles          # 缓存/限流/延迟队列 三角色演示
```

Celery 角色（另开一个终端先起 worker，Windows 必须 `--pool=solo`）：

```bash
uv run celery -A apps.redis_four_roles.celery_lab worker --pool=solo -l info
uv run python -m apps.redis_four_roles.celery_demo
```

## 四角色核心机制速览

### 1. 缓存（cache-aside）
- 读：`GET` 未命中 → 回源 loader → `SET` + TTL 回写
- 写：更新源后**删**缓存（而非更新缓存），避免并发写脏数据
- Redis 故障一律降级直查源，不拖垮业务

### 2. Celery 消息通道
- 同一个 Redis URL 同时当 `broker`（任务消息走 LIST）和 `result_backend`（结果走 STRING）
- `visibility_timeout` 必须 > 最长任务耗时，否则 unacked 消息被重投 → 任务双活（creativault 踩过的坑）
- `acks_late + reject_on_worker_lost + prefetch=1` ≈ 至少一次投递 + 公平调度
- 队列名带环境前缀，共享 Redis 上多环境互不串台

### 3. 延迟队列（为什么不用 Celery countdown？）
Celery 的 countdown 是"消息层面的延迟"（worker 收到后按 ETA 持有），长延迟任务会
占住 unacked 消息、受 visibility_timeout 反复重投。自建延迟队列是"存储层面的延迟"：

- `dq:pending:{q}` ZSET：score = 到期时间戳 → 天然按到期顺序排好的时间轮
- `dq:tasks:{q}` HASH：task_id → payload JSON
- `dq:processing:{q}` ZSET：领取时间戳，用于超时判定
- `dq:wakeup:{q}` LIST：入队 LPUSH 唤醒信号，Dispatcher BLPOP 阻塞等待（免空转轮询）
- 四个操作（ENQUEUE/DEQUEUE/ACK/RECLAIM）全部 Lua 原子执行，多 Worker 安全
- 处理方崩溃兜底：RECLAIM 把 processing 中超时未 ACK 的任务放回 pending（score=0 立即重试）

creativault 中 Dispatcher 到期后投递 Kafka；本 app 改为注入 `async handler`，与任何业务解耦。

### 4. 限流器（滑动窗口）
- 每次请求 `ZADD now member`，窗口外记录 `ZREMRANGEBYSCORE` 清掉，`ZCARD` 计数
- "清理 → 计数 → 判断 → 写入"必须在**一个 Lua 脚本**里原子完成，否则并发多放行
- 多窗口（如 QPS + 每分钟）采用"先全检查、全部通过才全写入"，避免部分写入污染计数

## 目录

```
apps/redis_four_roles/
├── __main__.py            # 入口：uv run python -m apps.redis_four_roles
├── config.py              # Settings（REDIS_URL 等）
├── demo.py                # 三角色联跑演示
├── celery_demo.py         # Celery 角色演示（生产者侧）
├── infra/
│   ├── redis_manager.py   # 共享连接池（四角色唯一入口）
│   ├── cache.py           # 角色一：缓存
│   ├── rate_limiter.py    # 角色四：限流器
│   └── delay_queue/
│       ├── models.py      # key 模板 + 常量
│       ├── lua_scripts.py # 四个 Lua 原子脚本
│       ├── manager.py     # enqueue/dequeue/ack/reclaim
│       └── dispatcher.py  # 常驻协程（BLPOP 唤醒 + reclaim 兜底）
└── celery_lab/
    ├── app.py             # Celery 实例（broker=backend=同一 Redis）
    └── tasks.py           # 示例任务（普通/countdown/重试）
```
