"""Kafka 公共辅助：topic、消息构造、消费端幂等表。"""

from __future__ import annotations

import json
import uuid

from apps.common.settings import settings

TOPIC_EVENTS = f"{settings.namespace}_events"
TOPIC_DEAD = f"{settings.namespace}_events_dead"

DDL = """
CREATE SCHEMA IF NOT EXISTS tlr_kf;
DROP TABLE IF EXISTS tlr_kf.processed_messages;
CREATE TABLE tlr_kf.processed_messages (
    message_id  TEXT PRIMARY KEY,
    topic       TEXT NOT NULL,
    partition_n INT NOT NULL,
    offset_n    BIGINT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def new_group(suffix: str) -> str:
    """每次实验用全新消费组：earliest 起读，避免上次运行遗留 offset 干扰。"""
    return f"{settings.namespace}_{suffix}_{uuid.uuid4().hex[:6]}"


def make_message(seq: int, body: str = "") -> tuple[str, str]:
    """返回 (key, value_json)。value 内嵌全局唯一 message_id 供消费幂等。"""
    value = json.dumps({"seq": seq, "message_id": uuid.uuid4().hex, "body": body})
    return f"key-{seq % 3}", value


def parse_message(raw: bytes | str) -> dict:
    return json.loads(raw)
