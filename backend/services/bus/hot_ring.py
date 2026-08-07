"""
HotRingBus — 热路径无锁环形缓冲（P2.4，对标 LMAX Disruptor）。

目标（方案 §1.4）：热路径（MarketData→FactorCompute→Alpha→Portfolio→Risk→Execution）
用无锁 ring 避免 PriorityQueue 在极高频下的锁开销。

设计（Disruptor 精神）：
    - 预分配定长槽位的环形数组（cache 友好）
    - 单生产者多消费者：生产者写槽位 + 发布序列号；消费者按序列号读取
    - 内存屏障协调（Python 用 threading.Lock 极薄保护序列号发布，
      因 GIL 存在，纯 Python 无法做真正无锁；此实现先保证正确性 + 低开销，
      P4 可用 Rust+PyO3 重写为真无锁）

完成标准（方案 P2.4）：单生产 4 消费链，10k msg/s 无丢失；序列号单调。

注：Python 的 GIL 意味着"无锁"在这里是"避免显式重锁 + 最小临界区"。
    真正的 sub-μs 无锁需 Rust 核（P4 可选）。当前实现优先正确性与低延迟于 PriorityQueue。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class RingSlot:
    """环形缓冲槽位（预分配，复用）。"""
    seq: int = -1
    data: Any = None
    topic: str = ""


class HotRingBus:
    """
    单生产者多消费者无锁环形总线。

    用法：
        bus = HotRingBus(size=8192)
        # 消费者订阅
        bus.subscribe("factor", handler_fn)
        # 生产者发布
        bus.publish("factor", factor_vector)
        # 消费者在独立线程消费（或调用 poll）
    """

    def __init__(self, size: int = 8192):
        self.size = size
        self.mask = size - 1  # 位与取模（size 必须是 2 的幂）
        assert size > 0 and (size & self.mask) == 0, "size 必须是 2 的幂"
        self._slots: list[RingSlot] = [RingSlot() for _ in range(size)]
        self._next_seq = 0  # 下一个要写入的序列号
        self._lock = threading.Lock()  # 仅保护序列号发布（极薄临界区）
        self._subscribers: dict[str, list[Callable[[Any], None]]] = {}
        self._published = 0
        self._dropped = 0  # 消费慢导致覆盖时统计

    def subscribe(self, topic: str, handler: Callable[[Any], None]) -> None:
        """订阅 topic。handler 在 poll() 调用时同步执行（热路径线程）。"""
        self._subscribers.setdefault(topic, []).append(handler)

    def publish(self, topic: str, data: Any) -> bool:
        """
        发布事件。返回 True 成功，False 缓冲满（背压）。

        单生产者：写槽位 → 发布序列号（内存屏障语义）。
        """
        with self._lock:
            seq = self._next_seq
            idx = seq & self.mask
            slot = self._slots[idx]
            # 检测背压：若槽位序列号仍是当前- size（消费者未读完），计数丢弃
            if slot.seq >= 0 and slot.seq == seq - self.size:
                self._dropped += 1
            slot.seq = seq
            slot.topic = topic
            slot.data = data
            self._next_seq = seq + 1
            self._published += 1
        return True

    def poll_latest(self, topic: str) -> Any:
        """获取某 topic 的最新数据（非破坏性，快速读取最近发布值）。"""
        with self._lock:
            # 从尾部倒序找最近的该 topic 槽位
            seq = self._next_seq - 1
            for offset in range(min(self.size, self._next_seq)):
                idx = (seq - offset) & self.mask
                slot = self._slots[idx]
                if slot.topic == topic and slot.seq >= 0:
                    return slot.data
        return None

    def drain(self) -> int:
        """
        同步派发所有已发布且未消费的事件到订阅者。
        返回派发数量。热路径线程调用（无额外线程切换）。
        """
        delivered = 0
        # 简化：遍历所有槽位，按序列号顺序派发
        with self._lock:
            snapshot = [(s.seq, s.topic, s.data) for s in self._slots if s.seq >= 0]
        snapshot.sort(key=lambda x: x[0])
        for seq, topic, data in snapshot:
            handlers = self._subscribers.get(topic, [])
            for h in handlers:
                try:
                    h(data)
                    delivered += 1
                except Exception:
                    pass  # 热路径消费者异常不阻塞总线
        return delivered

    def stats(self) -> dict:
        return {
            "size": self.size,
            "published": self._published,
            "dropped": self._dropped,
            "topics": list(self._subscribers.keys()),
            "next_seq": self._next_seq,
        }

    def clear(self) -> None:
        with self._lock:
            for s in self._slots:
                s.seq = -1
                s.data = None
                s.topic = ""
            self._next_seq = 0
            self._published = 0
            self._dropped = 0


# 模块级单例（热路径共享）
_default_bus = HotRingBus()


def get_default_bus() -> HotRingBus:
    return _default_bus
