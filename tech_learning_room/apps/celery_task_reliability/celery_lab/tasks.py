"""四类任务：重试退避 / 业务幂等 / 进度账本断点续跑 / 孤儿恢复。

所有任务都可以用 `.run(...)` 本地直调（绕过 broker，适合演示任务体逻辑），
也可以 `.delay(...)` 走真实 worker。
"""

from __future__ import annotations

import time

from apps.celery_task_reliability.celery_lab.app import celery_app, queue_name
from apps.celery_task_reliability.db import SCHEMA, connect

# 进程内故障计数：演示"前 N 次失败、之后成功"的瞬时故障
_FLAKY = {"attempts": 0}


@celery_app.task(
    name="tlr.flaky_sync",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,          # 指数退避：1s, 2s, 4s ...
    retry_backoff_max=60,
    retry_jitter=False,          # 教学演示关闭抖动，让退避节奏可预测
    max_retries=3,
)
def flaky_sync(source: str) -> str:
    """模拟瞬时故障：外部依赖前两次不可用，第三次恢复。"""
    _FLAKY["attempts"] += 1
    n = _FLAKY["attempts"]
    print(f"[worker] flaky_sync({source}) attempt #{n}")
    if n < 3:
        raise ConnectionError(f"{source} 暂不可用（瞬时故障，值得重试）")
    return f"ok after {n} attempts"


@celery_app.task(name="tlr.charge_user")
def charge_user(idem_key: str, user_id: int, amount: int, fail_first: int = 0) -> str:
    """业务幂等扣费：先查幂等键，命中直接返回；未命中才真正扣。

    fail_first>0 时抛异常且不留任何记录（失败不占键，调用方可安全重试）。
    """
    with connect() as conn:
        row = conn.execute(
            f"SELECT id, status FROM {SCHEMA}.charges WHERE idem_key = %s", (idem_key,)
        ).fetchone()
        if row is not None:
            conn.rollback()
            return f"duplicate(id={row[0]}, status={row[1]})"

        if fail_first > 0:
            conn.rollback()
            raise RuntimeError("模拟扣费链路故障")

        conn.execute(
            f"INSERT INTO {SCHEMA}.charges (idem_key, user_id, amount, status) "
            f"VALUES (%s, %s, %s, 'charged')",
            (idem_key, user_id, amount),
        )
        conn.commit()
        return "charged"


@celery_app.task(name="tlr.batch_send")
def batch_send_job(job_id: int, item_delay: float = 0.05) -> str:
    """批量发送：以 batch_items 表为进度账本，天然断点续跑。

    关键设计：
    - 入口先占位 processing 并立刻 commit（孤儿恢复据此判断'卡住'）
    - 每处理一条 commit 一次心跳（updated_at 前进 = 任务活着）
    - 只处理 status='pending' 的条目：重投后已 sent 的不会重复发送
    """
    with connect() as conn:
        conn.execute(
            f"UPDATE {SCHEMA}.batch_jobs SET status = 'processing', updated_at = now() WHERE id = %s",
            (job_id,),
        )
        conn.commit()

        pending = conn.execute(
            f"SELECT id, seq FROM {SCHEMA}.batch_items "
            f"WHERE job_id = %s AND status = 'pending' ORDER BY seq",
            (job_id,),
        ).fetchall()
        sent_before = conn.execute(
            f"SELECT COUNT(*) FROM {SCHEMA}.batch_items WHERE job_id = %s AND status = 'sent'",
            (job_id,),
        ).fetchone()[0]
        if not pending:
            conn.execute(
                f"UPDATE {SCHEMA}.batch_jobs SET status = 'done', updated_at = now() WHERE id = %s",
                (job_id,),
            )
            conn.commit()
            return f"job {job_id}: nothing to do (already done)"

        for item_id, seq in pending:
            time.sleep(item_delay)  # 模拟外部发送耗时
            conn.execute(
                f"UPDATE {SCHEMA}.batch_items SET status = 'sent' WHERE id = %s", (item_id,)
            )
            conn.execute(  # 心跳：证明本任务还在推进
                f"UPDATE {SCHEMA}.batch_jobs SET updated_at = now() WHERE id = %s", (job_id,)
            )
            conn.commit()

        conn.execute(
            f"UPDATE {SCHEMA}.batch_jobs SET status = 'done', updated_at = now() WHERE id = %s",
            (job_id,),
        )
        conn.commit()
        return f"job {job_id}: sent {len(pending)} items (resumed from {sent_before} sent)"


@celery_app.task(name="tlr.recover_orphans")
def recover_orphan_batches(
    orphan_seconds: int = 60, max_recovery: int = 3, requeue: bool = False
) -> dict:
    """孤儿恢复：扫描'processing 且心跳停止'的任务，重投或标失败。

    阈值链必须满足：孤儿判定时间 > 任务硬超时，否则活跃任务会被误判重投。
    requeue=True 时真实 .delay() 重投（需要 broker）；教学默认 False 只演示状态机。
    """
    summary = {"requeued": 0, "marked_failed": 0, "scanned": 0}
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, recovery_count FROM {SCHEMA}.batch_jobs "
            f"WHERE status = 'processing' "
            f"AND updated_at < now() - make_interval(secs => %s) "
            f"ORDER BY updated_at ASC",
            (orphan_seconds,),
        ).fetchall()
        summary["scanned"] = len(rows)
        for job_id, recovery_count in rows:
            if recovery_count >= max_recovery:
                conn.execute(
                    f"UPDATE {SCHEMA}.batch_jobs SET status = 'failed', updated_at = now(), "
                    f"label = label || ' [MAX_RECOVERY_EXCEEDED]' WHERE id = %s",
                    (job_id,),
                )
                summary["marked_failed"] += 1
            else:
                conn.execute(
                    f"UPDATE {SCHEMA}.batch_jobs SET recovery_count = recovery_count + 1, "
                    f"updated_at = now() WHERE id = %s",
                    (job_id,),
                )
                summary["requeued"] += 1
                if requeue:
                    batch_send_job.delay(job_id)
            conn.commit()
    return summary
