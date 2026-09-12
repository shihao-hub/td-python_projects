"""任务侧数据库（psycopg3 同步驱动：Celery worker 是同步进程）。"""

from __future__ import annotations

import psycopg

from apps.common.settings import settings

SCHEMA = "tlr_cel"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
DROP TABLE IF EXISTS {SCHEMA}.batch_items;
DROP TABLE IF EXISTS {SCHEMA}.batch_jobs;
DROP TABLE IF EXISTS {SCHEMA}.charges;

CREATE TABLE {SCHEMA}.charges (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idem_key    TEXT NOT NULL,
    user_id     INT  NOT NULL,
    amount      INT  NOT NULL,
    status      TEXT NOT NULL DEFAULT 'charged',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_charges_idem UNIQUE (idem_key)
);

CREATE TABLE {SCHEMA}.batch_jobs (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',   -- queued/processing/done/failed
    recovery_count  INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE {SCHEMA}.batch_items (
    id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id  BIGINT NOT NULL REFERENCES {SCHEMA}.batch_jobs(id),
    seq     INT NOT NULL,
    status  TEXT NOT NULL DEFAULT 'pending',   -- pending/sent
    CONSTRAINT uq_batch_item UNIQUE (job_id, seq)
);

CREATE INDEX idx_batch_items_pending ON {SCHEMA}.batch_items (job_id, seq) WHERE status = 'pending';
CREATE INDEX idx_batch_jobs_orphan ON {SCHEMA}.batch_jobs (updated_at) WHERE status = 'processing';
"""


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_psycopg_url)


def reset_schema() -> None:
    with connect() as conn:
        conn.execute(DDL)
        conn.commit()
