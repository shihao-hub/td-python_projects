"""postgres_query_tuning —— EXPLAIN、索引与分页的量化对照（asyncpg）。"""

from __future__ import annotations

from apps.common.lab import Timer, banner, check, conclude, fact, step
from apps.common.settings import settings

SCHEMA = "tlr_tune"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
DROP TABLE IF EXISTS {SCHEMA}.orders;
DROP TABLE IF EXISTS {SCHEMA}.users;
DROP TABLE IF EXISTS {SCHEMA}.event_logs;

CREATE TABLE {SCHEMA}.users (
    id        INT PRIMARY KEY,
    name      TEXT NOT NULL,
    plan      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE {SCHEMA}.orders (
    id         BIGINT PRIMARY KEY,
    user_id    INT NOT NULL REFERENCES {SCHEMA}.users(id),
    status     TEXT NOT NULL,
    amount     NUMERIC(12,2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    note       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE {SCHEMA}.event_logs (
    id         BIGINT PRIMARY KEY,
    level      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    payload    TEXT NOT NULL DEFAULT ''
);
"""

USERS_ROWS = 10_000
ORDERS_ROWS = 100_000
LOGS_ROWS = 200_000


async def connect():
    import asyncpg

    return await asyncpg.connect(settings.database_psycopg_url)


async def reset_and_seed() -> None:
    """建表并造数：1 万用户 / 10 万订单 / 20 万日志（pending 占 5%）。"""
    conn = await connect()
    try:
        await conn.execute(DDL)
        await conn.execute(
            f"""
            INSERT INTO {SCHEMA}.users (id, name, plan)
            SELECT i, 'user_' || i, (ARRAY['free','pro','team'])[1 + (i % 3)]
            FROM generate_series(1, {USERS_ROWS}) AS i
            """
        )
        await conn.execute(
            f"""
            INSERT INTO {SCHEMA}.orders (id, user_id, status, amount, created_at, note)
            SELECT i,
                   1 + (i % {USERS_ROWS}),
                   CASE WHEN i % 20 = 0 THEN 'pending'
                        WHEN i % 3 = 0  THEN 'paid'
                        ELSE 'done' END,
                   (i % 1000)::numeric / 10,
                   now() - (({ORDERS_ROWS} - i) * interval '30 seconds'),
                   repeat('x', 64)
            FROM generate_series(1, {ORDERS_ROWS}) AS i
            """
        )
        await conn.execute(
            f"""
            INSERT INTO {SCHEMA}.event_logs (id, level, created_at, payload)
            SELECT i,
                   CASE WHEN i % 400 = 0 THEN 'error' ELSE 'info' END,
                   now() - (({LOGS_ROWS} - i) * interval '15 seconds'),
                   repeat('y', 128)
            FROM generate_series(1, {LOGS_ROWS}) AS i
            """
        )
    finally:
        await conn.close()


async def explain(conn, sql: str, *params) -> str:
    """执行 EXPLAIN (ANALYZE, BUFFERS) 并返回完整文本。"""
    rows = await conn.fetch(f"EXPLAIN (ANALYZE, BUFFERS) {sql}", *params)
    return "\n".join(str(r[0]) for r in rows)


def _headline(plan_text: str) -> list[str]:
    """从 EXPLAIN 文本里抽出最值得看的行：扫描方式、行数、缓冲区、耗时。"""
    keys = ("Seq Scan", "Index Scan", "Index Only Scan", "Sort Method", "rows=", "Buffers", "Execution Time")
    return [line.strip() for line in plan_text.splitlines() if any(k in line for k in keys)][:8]


def _show(title: str, plan_text: str) -> None:
    step(title)
    for line in _headline(plan_text):
        print(f"      | {line}")
