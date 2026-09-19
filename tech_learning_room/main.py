"""tech_learning_room 实验目录入口。

用法（在 tech_learning_room/ 目录下执行）：

    uv run python main.py                       # 列出全部实验
    uv run python main.py doctor                # 检查外部服务连通性（PG/Redis/Kafka）
    uv run python main.py run <app> [--scenario all|name]
                                                 # 运行某个实验

每个实验也可独立运行：uv run python -m apps.<app> --list
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

# Windows 终端默认 GBK，统一 UTF-8 避免中文乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

# 实验注册表：顺序 = 推荐学习顺序（按后端面试优先级排列）
APPS: list[dict] = [
    {
        "name": "postgres_transactions",
        "goal": "并发扣减与事务隔离：丢失更新 / 条件 UPDATE / 行锁 / 唯一约束 / 死锁 / 隔离级别",
        "needs": ["postgres"],
    },
    {
        "name": "redis_cache_consistency",
        "goal": "缓存一致性：击穿合并回源 / 空值缓存 / TTL 抖动 / 失效竞态 / 分布式锁",
        "needs": ["redis"],
    },
    {
        "name": "postgres_query_tuning",
        "goal": "查询调优：EXPLAIN ANALYZE / 联合索引 / 游标分页 / N+1 / 部分索引",
        "needs": ["postgres"],
    },
    {
        "name": "sqlalchemy_session_lifecycle",
        "goal": "Session 生命周期：长事务耗尽连接池 / 短事务+DTO 快照 / Detached 实例 / 工作单元",
        "needs": ["postgres"],
    },
    {
        "name": "transactional_outbox",
        "goal": "事务性发件箱：双写故障窗口 / 同事务写入 / 租约领取 / 崩溃重投 / 消费幂等",
        "needs": ["postgres", "kafka"],
    },
    {
        "name": "celery_task_reliability",
        "goal": "任务可靠性：重试退避 / 业务幂等键 / 进度账本断点续跑 / 孤儿恢复",
        "needs": ["postgres", "redis"],
    },
    {
        "name": "kafka_delivery_semantics",
        "goal": "投递语义：分区顺序 / 消费组再均衡 / 手动提交 / at-most-once vs at-least-once / 死信",
        "needs": ["kafka"],
    },
    {
        "name": "billing_compensation",
        "goal": "计费与补偿：Decimal 金额 / 幂等扣费 / 批次扣减 / 补偿配对 / 重复退款防护 / 对账",
        "needs": ["postgres"],
    },
    {
        "name": "fastapi_layered_api",
        "goal": "分层架构：Router→Service→Repository / 依赖注入 / 统一响应 / 跨仓储事务回滚",
        "needs": ["postgres"],
    },
    {
        "name": "python_asyncio_concurrency",
        "goal": "异步并发：串行 vs 并发 / 阻塞事件循环 / TaskGroup 异常传播 / 超时取消 / 信号量",
        "needs": [],
    },
    {
        "name": "redis_four_roles",
        "goal": "（已有）一个 Redis 的四种角色：缓存 / Celery 通道 / 延迟队列 / 限流器",
        "needs": ["redis"],
    },
    {
        "name": "distributed_transaction",
        "goal": "（已有）2PC / 3PC 对照演示：投票、执行、协调者崩溃、超时中断",
        "needs": [],
    },
]

SERVICE_HINTS = {
    "postgres": "docker compose up -d postgres（默认 localhost:55432，见 .env.example）",
    "redis": "docker compose up -d redis（默认 localhost:6380）或使用本机已有 Redis",
    "kafka": "docker compose up -d kafka（默认 localhost:29092）",
}


def cmd_list() -> None:
    print("tech_learning_room 实验目录（推荐按此顺序学习）\n")
    for i, app in enumerate(APPS, 1):
        needs = "、".join(app["needs"]) if app["needs"] else "无外部依赖"
        print(f"  {i:2d}. {app['name']}")
        print(f"      学习目标: {app['goal']}")
        print(f"      依赖服务: {needs}")
        print(f"      运行:     uv run python -m apps.{app['name']} --list")
    print("\n环境自检: uv run python main.py doctor")
    print("教程文档: 父仓库 docs/projects/python_projects/tech_learning_room/")


def _tcp_probe(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} 可达"
    except OSError as exc:
        return False, f"{host}:{port} 不可达（{exc.__class__.__name__}）"


async def _probe_pg(url: str) -> tuple[bool, str]:
    import asyncpg

    parsed = urlparse(url if "://" in url else f"postgresql://{url}")
    host, port = parsed.hostname or "localhost", parsed.port or 5432
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(
                host=host,
                port=port,
                user=parsed.username or "lab",
                password=parsed.password or "lab",
                database=(parsed.path or "/lab").lstrip("/"),
            ),
            timeout=3.0,
        )
        await conn.close()
        return True, f"PostgreSQL {host}:{port} 连接并查询成功"
    except Exception as exc:
        return False, f"PostgreSQL {host}:{port} 失败: {exc.__class__.__name__}: {exc}"


async def _probe_redis(url: str) -> tuple[bool, str]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(url)
    try:
        pong = await asyncio.wait_for(client.ping(), timeout=3.0)
        return bool(pong), f"Redis PING -> {pong}"
    except Exception as exc:
        return False, f"Redis 失败: {exc.__class__.__name__}: {exc}"
    finally:
        await client.aclose()


async def cmd_doctor() -> int:
    from apps.common.settings import settings

    print("== 环境自检（以 .env / 环境变量为准）==\n")
    ok_all = True

    # PostgreSQL：优先真实连接
    try:
        ok, msg = await _probe_pg(settings.database_psycopg_url)
    except Exception as exc:  # noqa: BLE001
        ok, msg = False, f"PostgreSQL 探测异常: {exc!r}"
    ok_all &= ok
    print(f"[{'OK ' if ok else 'NG '}] PostgreSQL  {msg}")
    if not ok:
        print(f"      提示: {SERVICE_HINTS['postgres']}")

    ok, msg = await _probe_redis(settings.redis_url)
    ok_all &= ok
    print(f"[{'OK ' if ok else 'NG '}] Redis       {msg}")
    if not ok:
        print(f"      提示: {SERVICE_HINTS['redis']}")

    kafka_host, kafka_port = settings.kafka_bootstrap.split(":")
    ok, msg = _tcp_probe(kafka_host, int(kafka_port))
    ok_all &= ok
    print(f"[{'OK ' if ok else 'NG '}] Kafka       {msg}")
    if not ok:
        print(f"      提示: {SERVICE_HINTS['kafka']}")

    print("\n结论:", "全部依赖就绪" if ok_all else "存在不可用依赖，涉及该依赖的实验会显式报错退出")
    return 0 if ok_all else 1


def cmd_run(app_name: str, scenario: str) -> int:
    known = {app["name"] for app in APPS}
    if app_name not in known:
        print(f"未知实验: {app_name}（uv run python main.py 查看全部）")
        return 2
    cmd = [sys.executable, "-m", f"apps.{app_name}"]
    if scenario:
        cmd += ["--scenario", scenario]
    return subprocess.call(cmd)


def main() -> None:
    parser = argparse.ArgumentParser(prog="tech_learning_room", description="实验目录入口")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="列出全部实验")
    sub.add_parser("doctor", help="检查外部服务连通性")
    p_run = sub.add_parser("run", help="运行某个实验")
    p_run.add_argument("app", help="实验名，如 postgres_transactions")
    p_run.add_argument("--scenario", default="", help="场景名或 all")

    args = parser.parse_args()
    if args.command in (None, "list"):
        cmd_list()
    elif args.command == "doctor":
        raise SystemExit(asyncio.run(cmd_doctor()))
    elif args.command == "run":
        raise SystemExit(cmd_run(args.app, args.scenario))


if __name__ == "__main__":
    main()
