"""四个场景：双写问题 / 同事务发件箱 / 崩溃重投与幂等 / Kafka 端到端。"""

from __future__ import annotations

import json

from sqlalchemy import func, select, text

from apps.common.lab import banner, check, conclude, fact, step
from apps.transactional_outbox.outbox import (
    Session,
    claim_and_send,
    consume_message,
    create_order_with_outbox,
    expire_leases,
    reset_schema,
    wrap_payload_with_message_id,
)
from apps.transactional_outbox.transport import InMemoryTransport, KafkaTransport


async def _outbox_stats() -> dict:
    async with Session() as session:
        rows = (
            await session.execute(
                select(
                    text("status"), func.count()
                ).select_from(text("tlr_outx.outbox")).group_by(text("status"))
            )
        ).fetchall()
        return {status: count for status, count in rows}


# ──────────────────────────────────────────────────────────────────────────
# 场景 1：双写问题 —— 不用 outbox 直接发消息的两个故障窗口
# ──────────────────────────────────────────────────────────────────────────

async def scenario_dual_write_problem() -> None:
    banner("场景 1：双写问题 —— '先提交再发送'与'先发送再提交'都有窗口")
    await reset_schema()
    transport = InMemoryTransport()

    step("窗口 A：DB 提交成功 -> 发送失败（通道抖动）")
    async with Session() as session:
        order_id = await create_order_with_outbox(session, "book", 1)
    transport.fail_next = True
    try:
        # 朴素做法：提交后直接发
        await transport.send("m1", "order.created", json.dumps({"order_id": order_id}))
    except ConnectionError as exc:
        fact("发送结果", f"{exc}（事件永远丢失：没有任何机制会再试）")
    fact("订单状态", f"order={order_id} 已存在，下游永远不知道它被创建")
    check(len(transport.sent) == 0, "事件丢失：库存/积分/通知等下游与主库从此不一致", "未复现")

    step("窗口 B：发送成功 -> DB 提交前进程崩溃")
    from apps.transactional_outbox.outbox import OrderRow

    transport2 = InMemoryTransport()
    session = Session()
    order = OrderRow(product="pen", qty=2)
    session.add(order)
    await session.flush()
    await transport2.send("m2", "order.created", json.dumps({"order_id": order.id, "product": "pen", "qty": 2}))
    await session.rollback()  # 模拟提交前崩溃：事务回滚
    await session.close()
    async with Session() as s:
        exists = await s.get(OrderRow, order.id)
    fact("消息已发出", f"transport.sent={len(transport2.sent)} 条")
    fact("订单是否存在", exists is None)
    check(len(transport2.sent) == 1 and exists is None,
          "幽灵事件：下游收到了一个数据库里根本不存在（已回滚）的订单", "未复现")
    conclude(
        "双写无解的根源：DB 事务与消息通道是两个独立系统，不存在跨两者的原子提交。"
        "窗口 A 丢事件、窗口 B 产幽灵事件。发件箱模式的答案是：只写数据库，"
        "把'发消息'变成数据库行，再由独立进程把行变成消息——至少一次投递 + 消费幂等。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 2：同事务发件箱 —— 业务与事件同生共死
# ──────────────────────────────────────────────────────────────────────────

async def scenario_outbox_atomic() -> None:
    banner("场景 2：同事务发件箱 —— 订单与事件要么都在、要么都不在")
    await reset_schema()

    step("2.1 正常路径：订单 + outbox 一起提交")
    async with Session() as session:
        ok_id = await create_order_with_outbox(session, "keyboard", 3)
    fact("stats", await _outbox_stats())

    step("2.2 失败路径：写入后业务校验失败 -> 整体回滚")
    session = Session()
    try:
        await create_order_with_outbox(session, "mouse", 1, fail_after_write=True)
    except RuntimeError as exc:
        await session.rollback()
        fact("捕获", exc)
    finally:
        await session.close()

    async with Session() as s:
        from apps.transactional_outbox.outbox import OrderRow, OutboxRow

        orders = (await s.execute(select(OrderRow.id, OrderRow.product))).fetchall()
        events = (await s.execute(select(OutboxRow.aggregate_id))).fetchall()
    fact("orders", orders)
    fact("outbox events", events)
    check(len(orders) == 1 and len(events) == 1 and events[0][0] == ok_id,
          "失败的写入零残留：订单与事件一起回滚", "事务边界泄漏")
    conclude(
        "发件箱行不是'消息的副本'，而是'消息本体'——它在数据库事务内诞生，"
        "天然与业务状态原子一致。投递器只是把它搬运到真正的消息系统。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 3：崩溃重投 —— 发送成功但没标记，租约恢复 + 消费幂等兜底
# ──────────────────────────────────────────────────────────────────────────

async def scenario_dispatcher_crash() -> None:
    banner("场景 3：投递器崩溃 —— 重复发送不可避免，幂等让重复无害")
    await reset_schema()
    transport = InMemoryTransport()

    async with Session() as session:
        await create_order_with_outbox(session, "monitor", 1)

    step("第一轮投递：发送成功后、标记 sent 前进程'崩溃'")
    stats = await claim_and_send(transport, crash_before_mark=True)
    fact("第一轮 stats", stats)
    fact("通道中的消息", len(transport.sent))
    fact("outbox stats", await _outbox_stats())

    step("恢复：租约到期，被 expire_leases 重新放回 pending")
    expired = await expire_leases(lease_seconds=0)
    fact("恢复行数", expired)

    step("第二轮投递：同一条事件被再次发送（这就是至少一次）")
    stats = await claim_and_send(transport)
    fact("第二轮 stats", stats)
    fact("通道中的消息", len(transport.sent))
    dup = len(transport.sent) == 2 and transport.sent[0]["message_id"] == transport.sent[1]["message_id"]
    check(dup, "同一 message_id 出现两次：网络意义上的重复投递", "未复现")

    step("消费端：第一次 processed，第二次 duplicate")
    payload = wrap_payload_with_message_id(transport.sent[0]["payload"], transport.sent[0]["message_id"])
    r1 = await consume_message(payload)
    r2 = await consume_message(payload)
    fact("两次消费结果", f"{r1} / {r2}")
    check(r1 == "processed" and r2 == "duplicate", "幂等表挡住重复：业务副作用只发生一次", "幂等失效！")
    conclude(
        "链路闭环：崩溃 -> 租约恢复 -> 重发 -> 消费幂等。"
        "重复投递是分布式系统的常态而非事故，工程目标从「不重复」降级为「重复无害」。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 4：Kafka 端到端 —— 真实通道下的同一套机制
# ──────────────────────────────────────────────────────────────────────────

async def scenario_kafka_e2e() -> None:
    banner("场景 4：Kafka 端到端 —— 同事务写入 -> 真发 -> 真收 -> 幂等")
    import asyncio

    from aiokafka import AIOKafkaConsumer

    from apps.common.settings import settings
    from apps.transactional_outbox.transport import TOPIC

    await reset_schema()
    transport = KafkaTransport()

    step("创建订单（含 outbox 事件）")
    async with Session() as session:
        order_id = await create_order_with_outbox(session, "ssd", 2)

    step("投递：claim_and_send 经 KafkaTransport 真实发送")
    stats = await claim_and_send(transport)
    fact("stats", stats)
    await transport.close()

    step("消费：从 Kafka 拉取该消息并走幂等消费")
    consumer = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=f"{settings.namespace}_outbox_consumer",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    try:
        msg = await asyncio.wait_for(consumer.getone(), timeout=10)
        wrapped = wrap_payload_with_message_id(msg.value.decode(), msg.key.decode())
        r1 = await consume_message(wrapped, consumer="kafka-e2e")
        r2 = await consume_message(wrapped, consumer="kafka-e2e")  # 模拟重放
        fact("两次消费结果", f"{r1} / {r2}")
        async with Session() as s:
            from apps.transactional_outbox.outbox import OrderRow

            order = await s.get(OrderRow, order_id)
        fact("订单最终状态", order.status)
        check(r1 == "processed" and r2 == "duplicate" and order.status == "confirmed",
              "端到端闭环：事件可靠到达且只生效一次", "端到端失败")
        conclude(
            "真实通道下的关键点：①producer acks=all + send_and_wait，别用 fire-and-forget；"
            "②key 用 message_id 保证重发落同分区；③消费端幂等表与业务写同事务。"
            "至此'数据库与消息系统的一致性'问题在工程上闭环。"
        )
    finally:
        await consumer.stop()


SCENARIOS: dict[str, object] = {}
DESCRIPTIONS: dict[str, str] = {}


def _reg(name: str, fn, desc: str) -> None:
    SCENARIOS[name] = fn
    DESCRIPTIONS[name] = desc


_reg("dual-write-problem", scenario_dual_write_problem, "双写两个故障窗口：丢事件 / 幽灵事件（无需 Kafka）")
_reg("outbox-atomic", scenario_outbox_atomic, "同事务发件箱：业务与事件同生共死（无需 Kafka）")
_reg("dispatcher-crash", scenario_dispatcher_crash, "崩溃重投 + 租约恢复 + 消费幂等（无需 Kafka）")
_reg("kafka-e2e", scenario_kafka_e2e, "真实 Kafka 端到端：发送、消费、幂等闭环")


async def run_all() -> None:
    for name, fn in SCENARIOS.items():
        await fn()
