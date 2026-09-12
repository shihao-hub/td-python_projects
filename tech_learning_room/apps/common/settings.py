"""集中配置：从项目根 .env / 环境变量读取，整个项目只此一处。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Settings:
    """全局配置。教学版用 .env + 默认值；生产项目通常来自配置中心。"""

    # PostgreSQL
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", "postgresql+asyncpg://lab:lab@localhost:55432/lab"
        )
    )
    database_psycopg_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_PSYCOPG_URL", "postgresql://lab:lab@localhost:55432/lab"
        )
    )

    # Redis
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6380/0"))

    # Kafka
    kafka_bootstrap: str = field(
        default_factory=lambda: _env("KAFKA_BOOTSTRAP", "localhost:29092")
    )

    # Celery 队列前缀（共享 Redis 时做个人隔离）
    queue_prefix: str = field(default_factory=lambda: _env("LAB_QUEUE_PREFIX", "tlr"))

    # 实验命名空间：PG schema / Redis key / Kafka topic 的统一前缀
    namespace: str = "tlr"


settings = Settings()
