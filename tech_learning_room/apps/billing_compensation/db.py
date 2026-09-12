"""连接与表结构。

设计要点：
- 金额一律 NUMERIC(20,6) + Python Decimal，禁止二进制浮点
- credit_entries 是不可变账本：只 INSERT，不 UPDATE/DELETE
  （生产环境再加 DB 触发器 + 表权限双保险）
- 方向 direction ∈ {debit, credit}，amount 恒为正数，杜绝"负数表示支出"
"""

from __future__ import annotations

import psycopg

from apps.common.settings import settings

SCHEMA = "tlr_bill"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
DROP TABLE IF EXISTS {SCHEMA}.credit_entries;
DROP TABLE IF EXISTS {SCHEMA}.credit_lots;
DROP TABLE IF EXISTS {SCHEMA}.wallets;

CREATE TABLE {SCHEMA}.wallets (
    user_id    INT PRIMARY KEY,
    balance    NUMERIC(20,6) NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 积分批次（lot）：积分的"批次账"。消费按批次顺序消耗，
-- 补偿时按原批次回补——批次过期则新建补偿批次而非复活旧批次
CREATE TABLE {SCHEMA}.credit_lots (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     INT NOT NULL,
    amount      NUMERIC(20,6) NOT NULL,
    consumed    NUMERIC(20,6) NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'active',   -- active/exhausted/expired
    source_type TEXT NOT NULL DEFAULT 'grant',
    expires_at  TIMESTAMPTZ NOT NULL DEFAULT now() + interval '365 days',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_lots_user_active ON {SCHEMA}.credit_lots (user_id, created_at)
    WHERE status = 'active';

-- 不可变账本：每一笔 grant（credit）/ 消费（debit）/ 退款（credit）
-- idempotency_key 唯一 = 幂等第一道防线
-- related_group_key 把补偿分录指回原操作
CREATE TABLE {SCHEMA}.credit_entries (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id            INT NOT NULL,
    direction          TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    amount             NUMERIC(20,6) NOT NULL CHECK (amount > 0),
    group_key          TEXT NOT NULL,
    idempotency_key    TEXT NOT NULL,
    related_group_key  TEXT,
    note               TEXT NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_entry_idem UNIQUE (idempotency_key)
);
CREATE INDEX idx_entries_group ON {SCHEMA}.credit_entries (group_key);
"""


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_psycopg_url)


def reset_schema() -> None:
    with connect() as conn:
        conn.execute(DDL)
        conn.commit()


def ensure_wallet(user_id: int) -> None:
    with connect() as conn:
        conn.execute(
            f"INSERT INTO {SCHEMA}.wallets (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
            (user_id,),
        )
        conn.commit()
