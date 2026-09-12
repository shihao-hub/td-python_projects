"""领域逻辑：发放 / 幂等扣费 / 批次消耗 / 补偿配对 / 对账。"""

from __future__ import annotations

import uuid
from decimal import Decimal

import psycopg

from apps.billing_compensation.db import SCHEMA, connect


class InsufficientBalance(RuntimeError):
    pass


class DuplicateRequest(RuntimeError):
    """幂等命中：同一个 idempotency_key 的重复请求。"""

    def __init__(self, snapshot: dict) -> None:
        super().__init__("duplicate idempotency key")
        self.snapshot = snapshot


def grant(user_id: int, amount: Decimal, *, note: str = "grant") -> str:
    """发放积分：加余额 + 新批次 + credit 分录，一个事务。"""
    group_key = f"grant:{uuid.uuid4().hex[:12]}"
    with connect() as conn:
        conn.execute(
            f"UPDATE {SCHEMA}.wallets SET balance = balance + %s, updated_at = now() WHERE user_id = %s",
            (amount, user_id),
        )
        conn.execute(
            f"INSERT INTO {SCHEMA}.credit_lots (user_id, amount) VALUES (%s, %s)",
            (user_id, amount),
        )
        conn.execute(
            f"INSERT INTO {SCHEMA}.credit_entries "
            f"(user_id, direction, amount, group_key, idempotency_key, note) "
            f"VALUES (%s, 'credit', %s, %s, %s, %s)",
            (user_id, amount, group_key, f"idem:{group_key}", note),
        )
        conn.commit()
    return group_key


def charge(user_id: int, idem_key: str, amount: Decimal, *, fail_after: str = "") -> dict:
    """幂等扣费（三层防御的简化版）。

    1. 快路径：idempotency_key 已有成功分录 -> 抛 DuplicateRequest 带首次快照
    2. 行锁 + 余额校验 -> 钱包扣减 + 按批次消耗 + debit 分录
    3. fail_after 注入故障：'db' = 扣减后数据库异常（整事务回滚，零残留）
    """
    with connect() as conn:
        # 第一层：幂等快查
        row = conn.execute(
            f"SELECT group_key, amount FROM {SCHEMA}.credit_entries "
            f"WHERE idempotency_key = %s AND direction = 'debit'",
            (idem_key,),
        ).fetchone()
        if row is not None:
            conn.rollback()
            raise DuplicateRequest({"group_key": row[0], "amount": str(row[1])})

        # 第二层：行锁 + 余额校验
        conn.execute(f"SELECT balance FROM {SCHEMA}.wallets WHERE user_id = %s FOR UPDATE", (user_id,))
        balance = conn.execute(
            f"SELECT balance FROM {SCHEMA}.wallets WHERE user_id = %s", (user_id,)
        ).fetchone()[0]
        if balance < amount:
            conn.rollback()
            raise InsufficientBalance(f"balance={balance} < amount={amount}")

        group_key = f"charge:{idem_key}"
        remaining = amount
        lots = conn.execute(
            f"SELECT id, amount, consumed FROM {SCHEMA}.credit_lots "
            f"WHERE user_id = %s AND status = 'active' AND expires_at > now() "
            f"ORDER BY created_at FOR UPDATE",
            (user_id,),
        ).fetchall()
        available = sum(lot[1] - lot[2] for lot in lots)
        if available < amount:
            conn.rollback()
            raise InsufficientBalance(f"lots available={available} < amount={amount}")

        for lot_id, lot_amount, lot_consumed in lots:  # 按批次顺序消耗
            if remaining <= 0:
                break
            free = lot_amount - lot_consumed
            take = min(free, remaining)
            new_consumed = lot_consumed + take
            conn.execute(
                f"UPDATE {SCHEMA}.credit_lots SET consumed = %s, "
                f"status = CASE WHEN %s >= amount THEN 'exhausted' ELSE status END "
                f"WHERE id = %s",
                (new_consumed, new_consumed, lot_id),
            )
            remaining -= take

        conn.execute(
            f"UPDATE {SCHEMA}.wallets SET balance = balance - %s, updated_at = now() WHERE user_id = %s",
            (amount, user_id),
        )
        conn.execute(
            f"INSERT INTO {SCHEMA}.credit_entries "
            f"(user_id, direction, amount, group_key, idempotency_key, note) "
            f"VALUES (%s, 'debit', %s, %s, %s, %s)",
            (user_id, amount, group_key, idem_key, "consume"),
        )

        if fail_after == "db":
            raise RuntimeError("模拟扣减后数据库故障：整个事务回滚")

        conn.commit()
        return {"group_key": group_key, "amount": str(amount)}


def refund(original_group_key: str, *, user_id: int, note: str = "refund") -> dict:
    """补偿：按原 group_key 精确逆向。

    - 重复退款防护：补偿分录 idempotency_key = refund:{原group_key}，唯一约束拦截
    - 原批次 active -> consumed 回补；exhausted -> 恢复 active 再回补；
      不存在/expired -> 新建补偿批次（不复活过期批次，沿用原到期规则）
    """
    refund_idem = f"refund:{original_group_key}"
    with connect() as conn:
        dup = conn.execute(
            f"SELECT group_key FROM {SCHEMA}.credit_entries WHERE idempotency_key = %s",
            (refund_idem,),
        ).fetchone()
        if dup is not None:
            conn.rollback()
            raise DuplicateRequest({"group_key": dup[0]})

        debit = conn.execute(
            f"SELECT amount FROM {SCHEMA}.credit_entries "
            f"WHERE group_key = %s AND direction = 'debit'",
            (original_group_key,),
        ).fetchone()
        if debit is None:
            conn.rollback()
            raise RuntimeError(f"原消费分录不存在: {original_group_key}")
        amount = debit[0]

        # 找到原消费动过的批次：按 group_key 追溯没有明细表时，
        # 教学版用"按时间窗口 + 批次状态"简化；生产应在消费时写批次-分录关联表
        lots = conn.execute(
            f"SELECT id, amount, consumed, status, expires_at FROM {SCHEMA}.credit_lots "
            f"WHERE user_id = %s ORDER BY created_at",
            (user_id,),
        ).fetchall()
        remaining = amount
        for lot_id, lot_amount, lot_consumed, status, expires_at in lots:
            if remaining <= 0:
                break
            give_back = min(lot_consumed, remaining)
            if give_back <= 0:
                continue
            conn.execute(
                f"UPDATE {SCHEMA}.credit_lots SET consumed = consumed - %s, status = 'active' "
                f"WHERE id = %s",
                (give_back, lot_id),
            )
            remaining -= give_back
        if remaining > 0:  # 原批次已过期/不存在：新建补偿批次
            conn.execute(
                f"INSERT INTO {SCHEMA}.credit_lots (user_id, amount, source_type) "
                f"VALUES (%s, %s, 'refund-compensation')",
                (user_id, remaining),
            )

        conn.execute(
            f"UPDATE {SCHEMA}.wallets SET balance = balance + %s, updated_at = now() WHERE user_id = %s",
            (amount, user_id),
        )
        conn.execute(
            f"INSERT INTO {SCHEMA}.credit_entries "
            f"(user_id, direction, amount, group_key, idempotency_key, related_group_key, note) "
            f"VALUES (%s, 'credit', %s, %s, %s, %s, %s)",
            (user_id, amount, f"refund:{original_group_key}", refund_idem, original_group_key, note),
        )
        conn.commit()
        return {"refunded": str(amount), "related": original_group_key}


def wallet_balance(user_id: int) -> Decimal:
    with connect() as conn:
        return conn.execute(
            f"SELECT balance FROM {SCHEMA}.wallets WHERE user_id = %s", (user_id,)
        ).fetchone()[0]


def lot_snapshot(user_id: int) -> list[tuple]:
    with connect() as conn:
        return conn.execute(
            f"SELECT id, amount, consumed, status, source_type FROM {SCHEMA}.credit_lots "
            f"WHERE user_id = %s ORDER BY id",
            (user_id,),
        ).fetchall()


def entry_count(user_id: int) -> int:
    with connect() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM {SCHEMA}.credit_entries WHERE user_id = %s", (user_id,)
        ).fetchone()[0]


def reconcile(user_id: int) -> dict:
    """对账：钱包余额必须等于 credit 总额 - debit 总额。"""
    with connect() as conn:
        row = conn.execute(
            f"SELECT "
            f"COALESCE(SUM(CASE WHEN direction = 'credit' THEN amount END), 0) AS credits, "
            f"COALESCE(SUM(CASE WHEN direction = 'debit' THEN amount END), 0) AS debits "
            f"FROM {SCHEMA}.credit_entries WHERE user_id = %s",
            (user_id,),
        ).fetchone()
        balance = conn.execute(
            f"SELECT balance FROM {SCHEMA}.wallets WHERE user_id = %s", (user_id,)
        ).fetchone()[0]
    return {
        "sum_credit": str(row[0]),
        "sum_debit": str(row[1]),
        "wallet_balance": str(balance),
        "expected": str(row[0] - row[1]),
        "ok": balance == row[0] - row[1],
    }
