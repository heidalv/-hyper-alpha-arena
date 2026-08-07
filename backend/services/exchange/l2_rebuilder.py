"""
L2 订单簿重建器（P2.6，方案 §P2.6）。

目标：自建 L2 重建层（不依赖 ccxt/SDK 隐藏数据质量问题）。
    - 序列号 gap 检测：incoming_seq != prev_seq + 1 → 检测缺口
    - 自动 resync：gap 后标记 needs_resync，等下一个 snapshot 重置
    - 质量标记：gap/degraded 上报 DataQualityFlag（QualityGate 消费）

这是诊断（环境5缺陷2）的修复：现有依赖 SDK/ccxt 的 handleOrderBook 藏起数据质量。

完成标准（方案 P2.6）：注入 gap 测试用例，能检测+resync+告警。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class L2Book:
    """重建后的 L2 订单簿。"""
    seq: int
    bids: list[tuple[float, float]] = field(default_factory=list)  # (price, size) 降序
    asks: list[tuple[float, float]] = field(default_factory=list)  # (price, size) 升序
    ts_ns: int = 0

    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    def spread(self) -> Optional[float]:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return ba - bb

    def mid(self) -> Optional[float]:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2


@dataclass
class GapResult:
    """gap 检测结果（替代返回 L2Book，表示数据有问题）。"""
    gap: bool
    expected_seq: int
    got_seq: int
    quality: str = "GAP"


class L2Rebuilder:
    """
    单品种 L2 订单簿重建器。

    用法：
        reb = L2Rebuilder("BTC-PERP")
        book = reb.apply_snapshot(seq=100, bids=[...], asks=[...])  # 全量快照
        book = reb.apply_diff(seq=101, bids_update=[...], asks_update=[...])  # 增量
        if reb.needs_resync():
            # 上层重请求 snapshot
    """

    def __init__(self, symbol: str, max_levels: int = 100):
        self.symbol = symbol
        self.max_levels = max_levels
        self._book: Optional[L2Book] = None
        self._last_seq: int = -1
        self._needs_resync: bool = False

    def apply_snapshot(
        self,
        seq: int,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        ts_ns: int = 0,
    ) -> L2Book:
        """应用全量快照（重置本地簿）。"""
        self._book = L2Book(
            seq=seq,
            bids=sorted(bids, key=lambda x: -x[0])[: self.max_levels],
            asks=sorted(asks, key=lambda x: x[0])[: self.max_levels],
            ts_ns=ts_ns,
        )
        self._last_seq = seq
        self._needs_resync = False
        return self._book

    def apply_diff(
        self,
        seq: int,
        bids_update: list[tuple[float, float]],
        asks_update: list[tuple[float, float]],
        ts_ns: int = 0,
    ) -> L2Book | GapResult:
        """
        应用增量更新。返回 L2Book（成功）或 GapResult（检测到缺口）。

        缺口检测：seq != last_seq + 1 → gap，标记 resync。
        """
        if self._book is None:
            self._needs_resync = True
            return GapResult(gap=True, expected_seq=-1, got_seq=seq)

        # gap 检测
        if seq != self._last_seq + 1:
            self._needs_resync = True
            return GapResult(
                gap=True, expected_seq=self._last_seq + 1, got_seq=seq,
            )

        # 应用增量：更新价格档位
        bids_map = {p: s for p, s in self._book.bids}
        asks_map = {p: s for p, s in self._book.asks}

        for price, size in bids_update:
            if size == 0:
                bids_map.pop(price, None)
            else:
                bids_map[price] = size
        for price, size in asks_update:
            if size == 0:
                asks_map.pop(price, None)
            else:
                asks_map[price] = size

        self._book = L2Book(
            seq=seq,
            bids=sorted(bids_map.items(), key=lambda x: -x[0])[: self.max_levels],
            asks=sorted(asks_map.items(), key=lambda x: x[0])[: self.max_levels],
            ts_ns=ts_ns or self._book.ts_ns,
        )
        self._last_seq = seq
        return self._book

    def needs_resync(self) -> bool:
        """是否需要重新请求 snapshot（gap 后）。"""
        return self._needs_resync

    def get_book(self) -> Optional[L2Book]:
        return self._book

    def quality(self) -> str:
        """当前数据质量：GAP（需 resync）/ OK。"""
        return "GAP" if self._needs_resync else "OK"
