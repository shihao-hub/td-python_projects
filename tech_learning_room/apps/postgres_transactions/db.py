"""数据库连接与 schema 管理：每个实验 app 独立 schema，重置即删即建。"""

from __future__ import annotations

import psycopg

from apps.common.settings import settings

SCHEMA = "tlr_txn"

# 建表 DDL：显式列出全部字段；不用 JSONB，结构化字段表达业务
DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
DROP TABLE IF EXISTS {SCHEMA}.redemptions;
DROP TABLE IF EXISTS {SCHEMA}.redemption_codes;

CREATE TABLE {SCHEMA}.redemption_codes (
    id          INT PRIMARY KEY,
    code        TEXT NOT NULL,
    remaining   INT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE {SCHEMA}.redemptions (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code_id     INT NOT NULL REFERENCES {SCHEMA}.redemption_codes(id),
    user_id     INT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 唯一约束 = 数据库层面的最后一道幂等防线：
    -- 即使应用层校验被并发绕过，重复兑换也无法落库
    CONSTRAINT uq_redemption UNIQUE (code_id, user_id)
);
"""


def connect() -> psycopg.Connection:
    """默认事务模式（autocommit=False）：必须显式 commit/rollback，便于教学控制。"""
    return psycopg.connect(settings.database_psycopg_url)


def reset_schema() -> None:
    with connect() as conn:
        conn.execute(DDL)
        conn.commit()


def seed_code(code_id: int, code: str, remaining: int) -> None:
    with connect() as conn:
        conn.execute(
            f"INSERT INTO {SCHEMA}.redemption_codes (id, code, remaining) VALUES (%s, %s, %s)",
            (code_id, code, remaining),
        )
        conn.commit()


def read_remaining(conn: psycopg.Connection, code_id: int) -> int:
    row = conn.execute(
        f"SELECT remaining FROM {SCHEMA}.redemption_codes WHERE id = %s", (code_id,)
    ).fetchone()
    assert row is not None, f"code {code_id} 不存在"
    return int(row[0])
