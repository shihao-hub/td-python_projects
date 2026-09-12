"""五个场景：分区顺序 / 消费组分工 / at-most-once 丢消息 / at-least-once 重放 / 死信。"""

from __future__ import annotations

import asyncio
import json

import psycopg
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from apps.common.lab import banner, check, conclude, fact, step
from apps.common.settings import settings
from apps.kafka_delivery_semantics.kafka_infra import (
    TOPIC_DEAD,
    TOPIC_EVENTS,
    make_message,
    new_group,
    parse_message,
)

BOOTSTRAP = settings.kafka_bootstrap


def _db() -> psycopg.Connection:
    from apps.common.settings import settings as s

    return psycopg.connect(s.database_psycopg_url)


def _reset_processed() -> None:
    with _db() as conn:
        from apps.kafka_delivery_semantics.kafka_infra import DDL

        conn.execute(DDL)
        conn.commit()


async def _produce(messages: list[tuple[str, str]], topic: str = TOPIC_EVENTS) -> None:
    producer = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP, acks="all")
    await producer.start()
    try:
        for key, value in messages:
            await producer.send_and_wait(topic, key=key.encode(), value=value.encode())
    finally:
        await producer.stop()


async def _consume_n(
    group: str, n: int, *, handler, topic: str = TOPIC_EVENTS, timeout: float = 15.0
) -> list:
    """消费恰好 n 条后返回；handler(consumer, msg) 返回 truthy 则提前停止（模拟崩溃/重启）。"""
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=BOOTSTRAP,
        group_id=group,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    received = []
    try:
        while len(received) < n:
            msg = await asyncio.wait_for(consumer.getone(), timeout=timeout)
            received.append(msg)
            stop = await handler(consumer, msg)
            if stop:
                break
    finally:
        await consumer.stop()
    return received


def _record_processed(msg, note: str) -> str:
    """业务副作用 + 幂等标记同事务：返回 processed / duplicate。"""
    data = parse_message(msg.value)
    with _db() as conn:
        seen = conn.execute(
            "SELECT message_id FROM tlr_kf.processed_messages WHERE message_id = %s",
            (data["message_id"],),
        ).fetchone()
        if seen is not None:
            conn.rollback()
            return "duplicate"
        conn.execute(
            "INSERT INTO tlr_kf.processed_messages "
            "(message_id, topic, partition_n, offset_n, note) VALUES (%s, %s, %s, %s, %s)",
            (data["message_id"], msg.topic, msg.partition, msg.offset, note),
        )
        conn.commit()
        return "processed"


# ──────────────────────────────────────────────────────────────────────────
# 场景 1：分区与顺序 —— 同 key 进同分区，分区内严格有序
# ──────────────────────────────────────────────────────────────────────────

async def scenario_partition_order() -> None:
    banner("场景 1：分区与顺序 —— 同 key 的消息必然同分区、分区内有序")
    # 全部用 key-A：确定性落到同一个分区
    messages = [("key-A", json.dumps({"seq": i, "message_id": f"ord-{i}"})) for i in range(1, 11)]
    await _produce(messages)

    partitions: list[int] = []
    seqs: list[int] = []

    async def handler(consumer, msg):
        partitions.append(msg.partition)
        seqs.append(parse_message(msg.value)["seq"])
        await consumer.commit()
        return None

    await _consume_n(new_group("order"), 10, handler=handler)
    fact("消息落到分区", partitions)
    fact("收到顺序 seq", seqs)
    check(len(set(partitions)) == 1 and seqs == sorted(seqs),
          "同 key 全部进入同一分区，且按发送顺序到达", "顺序或分区异常")

    step("换 10 个不同 key：消息被均匀摊到 3 个分区")
    messages = [(f"k{i}", json.dumps({"seq": i, "message_id": f"spread-{i}"})) for i in range(10)]
    await _produce(messages)
    partitions2: dict[int, int] = {}

    async def handler2(consumer, msg):
        partitions2[msg.partition] = partitions2.get(msg.partition, 0) + 1
        await consumer.commit()
        return None

    await _consume_n(new_group("spread"), 10, handler=handler2)
    fact("分区分布", partitions2)
    check(len(partitions2) > 1, "key 哈希把消息摊到多个分区：并行度的来源", "未摊开")
    conclude(
        "Kafka 只保证分区内有序。要'某实体的消息有序'（同一订单的状态流转），"
        "就把该实体的 key 作为消息 key；要吞吐优先就多用几个 key。"
        "顺序与并行是同一个旋钮的两端。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 2：消费组 —— 分区是分工单位，一个分区同一时刻只归一个消费者
# ──────────────────────────────────────────────────────────────────────────

async def scenario_consumer_group() -> None:
    banner("场景 2：消费组 —— 两个消费者瓜分 3 个分区")
    group = new_group("cg")
    assignments: dict[int, list[int]] = {}
    counters: dict[int, int] = {1: 0, 2: 0}
    done = asyncio.Event()

    async def run_consumer(cid: int) -> None:
        consumer = AIOKafkaConsumer(
            TOPIC_EVENTS,
            bootstrap_servers=BOOTSTRAP,
            group_id=group,
            auto_offset_reset="latest",  # 只看本场景新发的消息
            enable_auto_commit=True,
        )
        await consumer.start()
        try:
            # 等再均衡完成，记录分配结果
            for _ in range(50):
                assignment = consumer.assignment()
                if assignment:
                    assignments[cid] = sorted({tp.partition for tp in assignment})
                    break
                await asyncio.sleep(0.2)
            # 收 6 条本场景消息
            got = 0
            while got < 6 and not done.is_set():
                try:
                    msg = await asyncio.wait_for(consumer.getone(), timeout=1.0)
                    counters[cid] += 1
                    got += 1
                except asyncio.TimeoutError:
                    continue
        finally:
            await consumer.stop()

    t1, t2 = asyncio.create_task(run_consumer(1)), asyncio.create_task(run_consumer(2))
    await asyncio.sleep(2.5)  # 等两个消费者加入、再均衡完成
    step(f"再均衡完成：消费者1 分到分区 {assignments.get(1)}，消费者2 分到分区 {assignments.get(2)}")
    union = set(assignments.get(1, [])) | set(assignments.get(2, []))
    overlap = set(assignments.get(1, [])) & set(assignments.get(2, []))
    check(len(union) == 3 and not overlap, "3 个分区被两个消费者瓜分且互不重叠", f"分配异常: {assignments}")

    messages = [(f"g{i}", json.dumps({"seq": i, "message_id": f"cg-{i}"})) for i in range(12)]
    await _produce(messages)
    await asyncio.sleep(4)
    done.set()
    await asyncio.gather(t1, t2, return_exceptions=True)
    fact("各消费者收到条数", counters)
    check(counters[1] + counters[2] >= 6, "每个分区只被其归属消费者消费", "消费不完整")
    conclude(
        "消费组 = 分区级负载均衡：消费者数 < 分区数时有空闲，相等时刚好一一对应，"
        "超出分区数的消费者完全闲置。扩容消费者之前先看分区数。"
        "再均衡期间（join/sync）会有短暂停止消费，这也是'处理到一半的任务'要能应对重平衡的原因。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 3：at-most-once —— 先提交后处理，崩溃即丢
# ──────────────────────────────────────────────────────────────────────────

async def scenario_at_most_once() -> None:
    banner("场景 3：at-most-once —— 先 commit 再处理，处理前崩溃 = 消息永久丢失")
    _reset_processed()
    group = new_group("amol")
    messages = [(f"m{i}", json.dumps({"seq": i, "message_id": f"amol-{i}"})) for i in range(1, 4)]
    await _produce(messages)

    step("第一轮消费：getone -> 立刻 commit -> 处理（第 1 条在'处理'前崩溃）")

    async def handler_crash(consumer, msg):
        await consumer.commit()  # 先提交：offset 已前移
        if parse_message(msg.value)["seq"] == 1:
            return "crash"  # 模拟处理前进程死亡
        _record_processed(msg, "at-most-once round1")
        return None

    await _consume_n(group, 2, handler=handler_crash)  # 收 2 条后停（模拟崩溃）

    step("第二轮消费（同 group 重启）：从已提交 offset 之后继续")
    seen: list[int] = []

    async def handler2(consumer, msg):
        seen.append(parse_message(msg.value)["seq"])
        _record_processed(msg, "at-most-once round2")
        await consumer.commit()
        return None

    await _consume_n(group, 2, handler=handler2)
    fact("重启后收到 seq", seen)
    with _db() as conn:
        rows = conn.execute(
            "SELECT note, COUNT(*) FROM tlr_kf.processed_messages GROUP BY note"
        ).fetchall()
    fact("业务表记录", dict(rows))
    check(1 not in seen, "seq=1 永远不会再来：已提交但未处理，at-most-once 丢了它", "未复现丢失")
    conclude(
        "先提交后处理 = 至多一次：不重复但会丢。适合日志、指标这类'丢一条无伤大雅'的数据。"
        "绝大多数业务（扣费、下单、通知）不能接受丢失——那就要反过来。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 4：at-least-once —— 先处理后提交，崩溃重放 + 幂等去重
# ──────────────────────────────────────────────────────────────────────────

async def scenario_at_least_once() -> None:
    banner("场景 4：at-least-once —— 处理完成但未提交，重启重放，幂等兜底")
    _reset_processed()
    group = new_group("aleast")
    messages = [(f"m{i}", json.dumps({"seq": i, "message_id": f"aleast-{i}"})) for i in range(1, 4)]
    await _produce(messages)

    step("第一轮：处理 seq=1（业务已生效）-> 崩溃在 commit 之前")

    async def handler_crash(consumer, msg):
        note = _record_processed(msg, "at-least-once")  # 先处理（业务+幂等同事务）
        if parse_message(msg.value)["seq"] == 1:
            return "crash"  # 处理完成、未来得及 commit
        await consumer.commit()
        return None

    await _consume_n(group, 2, handler=handler_crash)

    step("第二轮（同 group 重启）：seq=1 被重放")
    results: list[tuple[int, str]] = []

    async def handler2(consumer, msg):
        seq = parse_message(msg.value)["seq"]
        results.append((seq, _record_processed(msg, "at-least-once")))
        await consumer.commit()
        return None

    await _consume_n(group, 2, handler=handler2)
    fact("第二轮结果 (seq, 处理结果)", results)
    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM tlr_kf.processed_messages").fetchone()[0]
    fact("业务表总行数", total)
    replayed = [seq for seq, r in results if r == "duplicate"]
    check(1 in replayed and total == 3,
          "seq=1 重放被幂等表识别为 duplicate；3 条消息副作用各恰好一次", "重放未被去重！")
    conclude(
        "先处理后提交 = 至少一次：不丢但会重。重放窗口=处理完成到提交成功之间。"
        "配套必须是消费端幂等（message_id + 业务写同事务）。"
        "工程默认都选这套：丢失往往无法找回，而重复可以靠幂等消解。"
    )


# ──────────────────────────────────────────────────────────────────────────
# 场景 5：死信队列 —— 处理失败有界重试后旁路，不阻塞主流
# ──────────────────────────────────────────────────────────────────────────

async def scenario_dead_letter() -> None:
    banner("场景 5：死信队列 —— 毒丸卡住分区，重试 3 次后旁路")
    _reset_processed()
    group = new_group("dlq")
    # 同 key 保证同分区：good -> bad -> good 顺序固定；bad 不 commit 就会挡住后面的 good
    await _produce([
        ("lane-1", json.dumps({"seq": 1, "message_id": "dlq-good-1", "body": "ok"})),
        ("lane-1", json.dumps({"seq": 2, "message_id": "dlq-bad-1", "body": "poison"})),
        ("lane-1", json.dumps({"seq": 3, "message_id": "dlq-good-2", "body": "ok"})),
    ])

    max_retry = 3
    attempts: dict[str, int] = {}
    outcomes: list[str] = []

    async def handler(consumer, msg):
        data = parse_message(msg.value)
        mid = data["message_id"]
        if data.get("body") == "poison":
            attempts[mid] = attempts.get(mid, 0) + 1
            if attempts[mid] >= max_retry:
                await _produce([(mid, msg.value.decode())], topic=TOPIC_DEAD)
                outcomes.append(f"{mid}: dead-lettered（第 {attempts[mid]} 次尝试后旁路 + 提交主流 offset）")
                await consumer.commit()  # 跳过毒丸，让分区继续前进
                return None
            outcomes.append(f"{mid}: 处理失败（第 {attempts[mid]} 次），未提交 offset，分区被卡住")
            return "stop"  # 模拟消费者失败退出重启
        outcomes.append(f"{mid}: {_record_processed(msg, 'dead-letter-demo')}")
        await consumer.commit()
        return None

    step("第一轮：good-1 正常处理提交；bad-1 失败（不提交）-> 分区停摆")
    await _consume_n(group, 2, handler=handler)
    step("第二轮（重启）：offset 未动，bad-1 再次失败")
    await _consume_n(group, 1, handler=handler)
    step("第三轮（重启）：达到重试上限 -> 投死信 + 提交，good-2 终于被处理")
    await _consume_n(group, 2, handler=handler)
    for line in outcomes:
        fact("事件", line)

    step("验证死信 topic 收到坏消息")
    dead = await _consume_n(new_group("dead-inspect"), 1, handler=lambda c, m: None,
                            topic=TOPIC_DEAD, timeout=10)
    fact("死信内容", parse_message(dead[0].value))
    with _db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM tlr_kf.processed_messages").fetchone()[0]
    fact("主流业务表", f"{n} 条（两条 good 已生效，poison 零副作用）")
    ok = n == 2 and any("dead-lettered" in o for o in outcomes)
    check(ok, "坏消息进死信、好消息最终处理完成：分区从停摆中恢复", "死信流程异常")
    conclude(
        "毒丸消息（格式错/触发 bug）不 commit 会永远卡住同一分区的后续消息。"
        "重试必须有界：超限投死信 topic + 提交主流 offset，修复后可从死信重放。"
        "死信是「延迟处理 + 可观测」的缓冲区，不是垃圾桶。"
    )


SCENARIOS: dict[str, object] = {}
DESCRIPTIONS: dict[str, str] = {}


def _reg(name: str, fn, desc: str) -> None:
    SCENARIOS[name] = fn
    DESCRIPTIONS[name] = desc


_reg("partition-order", scenario_partition_order, "同 key 同分区有序；多 key 摊开并行")
_reg("consumer-group", scenario_consumer_group, "两个消费者瓜分分区（再均衡）")
_reg("at-most-once", scenario_at_most_once, "先提交后处理：崩溃即永久丢失")
_reg("at-least-once", scenario_at_least_once, "先处理后提交：重放 + 幂等去重")
_reg("dead-letter", scenario_dead_letter, "毒丸消息有界重试后进死信 topic")


async def run_all() -> None:
    for fn in SCENARIOS.values():
        await fn()
