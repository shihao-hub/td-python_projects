# tech_learning_room

后端技术学习实验集合：以可运行、可复现故障的方式学习 PostgreSQL / Redis / Celery / Kafka / FastAPI / asyncio 的核心机制。每个实验独立成 app，按下述顺序学习（按后端面试优先级排列）。

## 快速开始

```powershell
uv sync                       # 安装全部依赖
cp .env.example .env          # 连接串默认指向 docker-compose 提供的服务

# 外部服务按需启动（内存有限时不要同时开）
docker compose up -d postgres # localhost:55432
docker compose up -d redis    # localhost:6380
docker compose up -d kafka    # localhost:29092

uv run python main.py doctor  # 连通性自检
uv run python main.py list    # 实验目录
uv run python main.py run postgres_transactions --scenario deadlock
```

## 实验清单

| app | 主题 | 依赖 |
|---|---|---|
| `postgres_transactions` | 丢失更新 / 条件 UPDATE / 行锁 / 唯一约束 / 死锁 / 隔离级别 | PG |
| `redis_cache_consistency` | 击穿 / 穿透 / 雪崩 / 失效竞态 / 分布式锁 | Redis |
| `postgres_query_tuning` | EXPLAIN / 联合索引 / 游标分页 / N+1 / 部分索引 | PG |
| `sqlalchemy_session_lifecycle` | 连接池耗尽 / 短事务+DTO / Detached 实例 / 工作单元 | PG |
| `transactional_outbox` | 双写问题 / 同事务发件箱 / 租约 / 消费幂等 | PG（Kafka 可选） |
| `celery_task_reliability` | 重试退避 / 幂等键 / 进度账本 / 孤儿恢复 | PG + Redis |
| `kafka_delivery_semantics` | 分区顺序 / 消费组 / 提交语义 / 死信 | Kafka |
| `billing_compensation` | Decimal 金额 / 幂等扣费 / 补偿配对 / 对账 | PG |
| `fastapi_layered_api` | Router→Service→Repository / DI / 统一响应 / 事务回滚 | PG |
| `python_asyncio_concurrency` | 事件循环 / TaskGroup / 超时取消 / 信号量 | 无 |
| `redis_four_roles` | 一个 Redis 的四种角色（缓存/broker/延迟队列/限流） | Redis |
| `distributed_transaction` | 2PC / 3PC 对照演示 | 无 |

## 约定

- 每个实验独立 schema / key 前缀 / topic 前缀，可反复重跑互不污染；
- 依赖不可用时显式报错退出，不静默降级成模拟实现；
- 单元测试：`uv run pytest`（无需外部服务）。

## 教程文档

原理详解见父仓库 `docs/projects/python_projects/tech_learning_room/`（每 app 一篇 + 总览 README）。
