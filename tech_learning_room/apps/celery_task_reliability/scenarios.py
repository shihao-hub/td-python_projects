"""五个场景：幂等键 / 失败不占键 / 断点续跑 / 孤儿恢复 / 真实 worker 重试。"""

from __future__ import annotations

import asyncio
import time

import redis.asyncio as aioredis

from apps.celery_task_reliability.celery_lab.tasks import (
    batch_send_job,
    charge_user,
    flaky_sync,
    recover_orphan_batches,
)
from apps.celery_task_reliability.db import SCHEMA, connect, reset_schema
from apps.common.lab import Timer, banner, check, conclude, fact, step
from apps.common.settings import settings


def _seed_job(n_items: int, *, sent_prefix: int = 0, job_status: str = "queued",
              age_updated_at_seconds: int | None = None) -> int:
    with connect() as conn:
        job_id = conn.execute(
            f"INSERT INTO {SCHEMA}.batch_jobs (label, status) VALUES (%s, %s) RETURNING id",
            (f"demo-job-{n_items}", job_status),
        ).fetchone()[0]
        for seq in range(1, n_items + 1):
            status = "sent" if seq <= sent_prefix else "pending"
            conn.execute(
                f"INSERT INTO {SCHEMA}.batch_items (job_id, seq, status) VALUES (%s, %s, %s)",
                (job_id, seq, status),
            )
        if age_updated_at_seconds is not None:
            conn.execute(
                f"UPDATE {SCHEMA}.batch_jobs SET updated_at = now() - make_interval(secs => %s) "
                f"WHERE id = %s",
                (age_updated_at_seconds, job_id),
            )
        conn.commit()
        return job_id


# ──────────────────────────────────────────────────────────────────────────
# 场景 1：业务幂等键 —— 消息重投/双击重试都不会重复扣费
# ──────────────────────────────────────────────────────────────────────────

def scenario_idempotency() -> None:
    banner("场景 1：业务幂等键 —— 同一个 idem_key 只生效一次")
    reset_schema()
    step("第一次提交（正常）")
    r1 = charge_user.run("pay:order:1001", user_id=42, amount=9)
    fact("结果", r1)
    step("网络重试/消息重投，同一 idem_key 再次到达")
    r2 = charge_user.run("pay:order:1001", user_id=42, amount=9)
    fact("结果", r2)
    with connect() as conn:
        n = conn.execute(f"SELECT COUNT(*) FROM {SCHEMA}.charges").fetchone()[0]
        row = conn.execute(
            f"SELECT idem_key, user_id, amount, status FROM {SCHEMA}.charges LIMIT 1"
        ).fetchone()
    fact("charges 行数", n)
    fact("唯一记录", row)
    check(r1 == "charged" and r2.startswith("duplicate") and n == 1,
          "第二次调用被幂等键短路：没有产生第二条扣费", "幂等失效！")
    conclude(
        "acks_late/重试/重投都可能让同一业务动作被执行多次，"
        "「至少一次投递 + 业务幂等键」是标准组合：键的选取要业务唯一（订单号+动作），"
        "存在唯一约束里，查插并发安全（见 postgres_transactions 场景 4）。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 2：失败不占键 —— 处理失败回滚，调用方可安全重试
# ──────────────────────────────────────────────────────────────────────────

def scenario_idempotency_crash() -> None:
    banner("场景 2：失败不占幂等键 —— 先占键再处理 vs 失败零残留")
    reset_schema()
    step("第一次执行失败（外部依赖故障）")
    try:
        charge_user.run("pay:order:2002", user_id=7, amount=5, fail_first=1)
    except RuntimeError as exc:
        fact("捕获", exc)
    with connect() as conn:
        n = conn.execute(f"SELECT COUNT(*) FROM {SCHEMA}.charges").fetchone()[0]
    fact("失败后 charges 行数", n)
    step("重试同一 idem_key（故障已恢复）")
    r = charge_user.run("pay:order:2002", user_id=7, amount=5)
    fact("结果", r)
    check(n == 0 and r == "charged",
          "失败事务零残留，重试按新请求处理并成功", "失败留下了半成品记录！")
    conclude(
        "幂等记录必须与业务写同事务：失败一起回滚（不占键），成功一起提交（占键）。"
        "若先占键再处理且不回滚，故障会把幂等键'烧掉'，之后永远 duplicate 却从未真正生效。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 3：进度账本断点续跑 —— 重投后只补发未完成的部分
# ──────────────────────────────────────────────────────────────────────────

def scenario_batch_resume() -> None:
    banner("场景 3：进度账本 —— 执行到一半被重投，从断点继续")
    reset_schema()
    job_id = _seed_job(n_items=8, sent_prefix=4, job_status="processing")
    step("造一个'发到第 4 条时崩溃'的现场：8 条中 4 条已 sent，job 停在 processing")

    with Timer() as t:
        result = batch_send_job.run(job_id, item_delay=0.05)
    fact("任务返回", result)
    fact("耗时", f"{t.ms:.0f}ms（只处理 4 条 pending，而非全部 8 条）")

    with connect() as conn:
        statuses = conn.execute(
            f"SELECT status, COUNT(*) FROM {SCHEMA}.batch_items WHERE job_id = %s GROUP BY status",
            (job_id,),
        ).fetchall()
        job_status = conn.execute(
            f"SELECT status FROM {SCHEMA}.batch_jobs WHERE id = %s", (job_id,)
        ).fetchone()[0]
    fact("条目状态分布", dict(statuses))
    fact("job 状态", job_status)
    check(job_status == "done" and dict(statuses).get("sent") == 8,
          "断点续跑完成：已 sent 的 4 条没有被重发，pending 的 4 条补发完毕", "续跑逻辑异常")
    conclude(
        "进度不依赖 Celery 的 result backend，而是落在业务表里：每条一个状态、逐条 commit。"
        "任务重投/孤儿恢复后自然从 pending 继续。这就是'状态账本外置'——"
        "Celery 只是触发通道，恢复的真相在数据库。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 4：孤儿恢复 —— 心跳停止的任务被扫描、重投、有界重试
# ──────────────────────────────────────────────────────────────────────────

def scenario_orphan_recovery() -> None:
    banner("场景 4：孤儿恢复 —— processing 且心跳超时的任务被捞起")
    reset_schema()
    live = _seed_job(4, job_status="processing")                     # 刚刚更新，心跳正常
    orphan = _seed_job(4, job_status="processing", age_updated_at_seconds=600)  # 10 分钟无心跳
    doomed = _seed_job(4, job_status="processing", age_updated_at_seconds=600)
    with connect() as conn:
        conn.execute(
            f"UPDATE {SCHEMA}.batch_jobs SET recovery_count = 3 WHERE id = %s", (doomed,)
        )
        conn.commit()

    step("运行恢复扫描（orphan_seconds=60，requeue=False 只演示状态机）")
    summary = recover_orphan_batches.run(orphan_seconds=60, max_recovery=3, requeue=False)
    fact("扫描结果", summary)

    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, status, recovery_count FROM {SCHEMA}.batch_jobs ORDER BY id"
        ).fetchall()
    for row in rows:
        tag = "心跳正常(不动)" if row[0] == live else ("重投+1" if row[0] == orphan else "超限标 failed")
        fact(f"job {row[0]}", f"status={row[1]} recovery_count={row[2]}  <- {tag}")

    ok = (
        summary["scanned"] == 2
        and summary["requeued"] == 1
        and summary["marked_failed"] == 1
        and dict((r[0], r[1]) for r in rows)[live] == "processing"
    )
    check(ok, "活跃任务不被误判；孤儿重投且计数+1；恢复超上限的任务标 failed 终止无限循环", "恢复逻辑异常")
    conclude(
        "阈值链条要记牢：孤儿判定时间 > 任务硬超时 > 软超时。"
        "recovery_count 上限防止「必败任务」无限重投（雪崩）；重投前先 commit 占位，"
        "防止多实例扫描造成双投。生产上该任务由 Beat 周期触发。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 5：真实 worker 的重试退避（需要 Redis broker + 单独终端起 worker）
# ──────────────────────────────────────────────────────────────────────────

WORKER_CMD = (
    "uv run celery -A apps.celery_task_reliability.celery_lab worker --pool=solo -l info"
)


async def _broker_ok() -> bool:
    client = aioredis.from_url(settings.redis_url)
    try:
        return bool(await client.ping())
    except Exception:  # noqa: BLE001
        return False
    finally:
        await client.aclose()


def scenario_retry_backoff() -> None:
    banner("场景 5：真实 worker 重试退避 —— 1s/2s/4s 指数退避后自愈")
    import asyncio

    if not asyncio.run(_broker_ok()):
        print(f"\n!! 需要 Redis broker（{settings.redis_url}）\n   docker compose up -d redis")
        raise SystemExit(3)

    print(f"\n  请先在另一个终端启动 worker（Windows 必须 solo pool）：\n  {WORKER_CMD}")
    print("  按回车继续（worker 就绪后）...", end="", flush=True)
    input()

    from celery.result import AsyncResult

    from apps.celery_task_reliability.celery_lab.tasks import flaky_sync

    result: AsyncResult = flaky_sync.delay("inventory-db")
    step("任务已投递（前两次模拟失败），观察 worker 终端的 attempt 输出...")
    t0 = time.perf_counter()
    while not result.ready():
        time.sleep(0.5)
        if time.perf_counter() - t0 > 60:
            print("  !! 60s 未完成：请确认 worker 已启动并监听 tlr_default 队列")
            return
    elapsed = time.perf_counter() - t0
    fact("最终结果", result.get())
    fact("总耗时", f"{elapsed:.1f}s（含 1+2=3s 退避等待 + 3 次执行）")
    check(result.successful(), "瞬时故障被指数退避重试治愈，调用方无需人工介入", "任务最终失败")
    conclude(
        "autoretry_for 圈定「值得重试」的异常（瞬时故障），"
        "不可重试错误（参数错、余额不足）直接失败才对——盲目 for=(Exception,) 会把 bug 刷成告警风暴。"
        "retry_backoff 让重试间隔指数增长，避免故障期打死外部依赖。"
    )


SCENARIOS: dict[str, object] = {}
DESCRIPTIONS: dict[str, str] = {}


def _reg(name: str, fn, desc: str) -> None:
    SCENARIOS[name] = fn
    DESCRIPTIONS[name] = desc


_reg("idempotency", scenario_idempotency, "幂等键：重复提交只扣一次费（无需 worker）")
_reg("idempotency-crash", scenario_idempotency_crash, "失败不占键：回滚后可安全重试（无需 worker）")
_reg("batch-resume", scenario_batch_resume, "进度账本：断点续跑只补发未完成（无需 worker）")
_reg("orphan-recovery", scenario_orphan_recovery, "孤儿恢复：扫描/重投/有界重试（无需 worker）")
_reg("retry-backoff", scenario_retry_backoff, "真实 worker：指数退避重试（需 Redis + worker）")


def run_all() -> None:
    for fn in SCENARIOS.values():
        fn()
