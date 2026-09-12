"""消息传输层：协议抽象 + 内存实现（可注入故障）+ Kafka 实现。

InMemoryTransport 让'发送失败/进程崩溃'这类故障可以精确复现，
KafkaTransport 走 aiokafka 真实链路——两者满足同一协议，核心逻辑不感知差异。
"""

from __future__ import annotations

from typing import Protocol

from apps.common.settings import settings

TOPIC = f"{settings.namespace}_outbox_events"


class MessageTransport(Protocol):
    """发件箱的投递通道协议：只要求 send 成功返回、失败抛异常。"""

    async def send(self, message_id: str, event_type: str, payload: str) -> None: ...


class InMemoryTransport:
    """内存通道：记录全部'已发出'的消息，支持注入下一次发送失败。"""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.fail_next = False

    async def send(self, message_id: str, event_type: str, payload: str) -> None:
        if self.fail_next:
            self.fail_next = False
            raise ConnectionError("模拟消息通道故障：发送失败")
        self.sent.append({"message_id": message_id, "event_type": event_type, "payload": payload})

    def message_ids(self) -> list[str]:
        return [m["message_id"] for m in self.sent]


class KafkaTransport:
    """真实 Kafka 通道：send_and_wait 等待 broker ACK 才算成功。"""

    def __init__(self, topic: str = TOPIC) -> None:
        self.topic = topic
        self._producer = None

    async def _ensure_producer(self):
        if self._producer is None:
            from aiokafka import AIOKafkaProducer

            self._producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap,
                acks="all",
                linger_ms=5,
            )
            await self._producer.start()
        return self._producer

    async def send(self, message_id: str, event_type: str, payload: str) -> None:
        producer = await self._ensure_producer()
        # key=message_id：同一事件重发时落到同一分区，消费者可见顺序稳定
        await producer.send_and_wait(
            self.topic,
            key=message_id.encode(),
            value=payload.encode(),
        )

    async def close(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
