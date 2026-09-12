"""表结构与核心机制：同事务双写 / 租约领取 / 崩溃重投 / 消费幂等。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, String, Text, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from apps.common.settings import settings

SCHEMA = "tlr_outx"

engine = create_async_engine(settings.database_url, pool_size=5)
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class OrderRow(Base):
    """业务表：订单。"""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product: Mapped[str] = mapped_column(String(64))
    qty: Mapped[int]
    status: Mapped[str] = mapped_column(String(16), default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxRow(Base):
    """发件箱：与业务同事务写入的'待发事件'。

    状态机：pending -> leased -> sent；leased 超时 -> 回到 pending（租约恢复）。
    attempts 超上限 -> dead（死信，人工处理）。
    """

    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)  # JSON 文本：结构化字段内嵌
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessedMessageRow(Base):
    """消费端幂等表：message_id 唯一约束 = 消费侧的最后一道防线。"""

    __tablename__ = "processed_messages"

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    consumer: Mapped[str] = mapped_column(String(64))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def reset_schema() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        await conn.run_sync(Base.metadata.create_all)


# ──────────────────────────────────────────────────────────────────────────
# 生产侧：同事务双写
# ──────────────────────────────────────────────────────────────────────────

async def create_order_with_outbox(
    session, product: str, qty: int, *, fail_after_write: bool = False
) -> str:
    """订单 + outbox 事件在同一事务写入；fail_after_write 模拟写入后、提交前的业务失败。"""
    order = OrderRow(product=product, qty=qty)
    session.add(order)
    await session.flush()  # 拿到 order.id

    event = OutboxRow(
        aggregate_id=order.id,
        event_type="order.created",
        payload=json.dumps({"order_id": order.id, "product": product, "qty": qty}),
    )
    session.add(event)
    if fail_after_write:
        raise RuntimeError("模拟业务校验失败：订单与事件必须一起回滚")
    await session.commit()
    return order.id


# ──────────────────────────────────────────────────────────────────────────
# 投递侧：短事务领取（租约）-> 无连接发送 -> 短事务标记
# ──────────────────────────────────────────────────────────────────────────

async def claim_and_send(
    transport, *, batch: int = 10, crash_before_mark: bool = False, max_attempts: int = 3
) -> dict:
    """一轮投递。返回统计信息。

    三段式：
      事务1（短）：FOR UPDATE SKIP LOCKED 领取 pending 行 -> 置 leased + 租约 -> commit
      发送（不持有任何数据库连接）
      事务2（短）：成功 -> sent；失败 -> 回 pending 且退避（attempts+1，超上限 -> dead）
    """
    async with Session() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxRow)
                    .where(OutboxRow.status == "pending", OutboxRow.next_attempt_at <= func.now())
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        claimed: list[OutboxRow] = []
        for row in rows:
            row.status = "leased"
            row.lease_token = str(uuid.uuid4())
            row.leased_at = func.now()
            row.attempts += 1
            claimed.append(row)
        await session.commit()

    stats = {"claimed": len(claimed), "sent": 0, "failed": 0, "crashed": 0, "dead": 0}
    for row in claimed:
        try:
            await transport.send(row.id, row.event_type, row.payload)
        except Exception:  # noqa: BLE001 —— 发送失败：退避后回 pending
            async with Session() as session:
                db_row = await session.get(OutboxRow, row.id)
                db_row.status = "pending"
                db_row.next_attempt_at = func.now()
                db_row.lease_token = None
                if db_row.attempts >= max_attempts:
                    db_row.status = "dead"
                    stats["dead"] += 1
                await session.commit()
            stats["failed"] += 1
            continue

        if crash_before_mark:
            # 注入崩溃：发送成功、标记之前进程'死亡'（不写库直接返回）
            # 结果：行停在 leased，等租约过期被 expire_leases 重新捞起 -> 重复发送
            stats["crashed"] += 1
            continue

        async with Session() as session:
            db_row = await session.get(OutboxRow, row.id)
            db_row.status = "sent"
            db_row.lease_token = None
            await session.commit()
        stats["sent"] += 1
    return stats


async def expire_leases(*, lease_seconds: float = 0.0) -> int:
    """把超时的 leased 行放回 pending。生产上是周期任务，这里手动触发。"""
    async with Session() as session:
        cutoff = func.now() - text(f"interval '{int(lease_seconds)} seconds'")
        rows = (
            (
                await session.execute(
                    select(OutboxRow).where(
                        OutboxRow.status == "leased", OutboxRow.leased_at < cutoff
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.status = "pending"
            row.lease_token = None
            row.leased_at = None
        await session.commit()
        return len(rows)


# ──────────────────────────────────────────────────────────────────────────
# 消费侧：幂等处理（业务写 + 幂等标记同事务）
# ──────────────────────────────────────────────────────────────────────────

async def consume_message(payload: str, *, consumer: str = "demo", fail: bool = False) -> str:
    """消费一条事件。返回 processed / duplicate / failed。

    幂等三步：先查标记 -> 业务处理 -> 标记写入，全部在同一事务。
    """
    data = json.loads(payload)
    async with Session() as session:
        seen = await session.get(ProcessedMessageRow, data["message_id"])
        if seen is not None:
            await session.rollback()
            return "duplicate"

        if fail:  # 模拟处理故障：不留标记，事件可安全重投
            raise RuntimeError("模拟消费处理失败")

        order = await session.get(OrderRow, data["order_id"])
        if order is not None:
            order.status = "confirmed"
        session.add(ProcessedMessageRow(message_id=data["message_id"], consumer=consumer))
        await session.commit()
    return "processed"


def wrap_payload_with_message_id(payload: str, message_id: str) -> str:
    """发送层把 message_id 注入 payload，消费端据此幂等。"""
    data = json.loads(payload)
    data["message_id"] = message_id
    return json.dumps(data)
