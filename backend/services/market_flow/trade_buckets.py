# -*- coding: utf-8 -*-
"""[F171] 按**成交自身时间戳**分桶的缓冲（纯逻辑，可单测）。

**修复的问题（数据层根因）**：`base_collector` 原先把整批成交塞进
"**落库时刻**所在的 15s 桶"（`floor(flush_time/15s)`），而 trades 轮询是 30s 一次
⇒ 一次轮询的 ~30s 成交全被记进**同一个桶**，相邻桶**整片为空** ✗。实测后果：
  · 15s 网格覆盖率只有 **47.5%**（盘口快照 90.8%）；
  · 桶的时间戳最多偏差 **15~30s** ⇒ "哪笔成交打到哪张单"存在同样量级的不确定 ✗，
    这让回放高出实盘 ~2.5bp（F148），也让"实盘账本"这个判据本身不可信 ✗。

**新语义**：成交进入**它自己时间戳所在的桶** ✓；桶**结束后才写库**（避免半成品被消费 ✓）；
写库是**幂等的**（写"累计值"，因此迟到的成交可以补写进已写过的桶 ✓），
并在**宽限期**（默认 2 个桶）内保留缓冲以吸收迟到成交，之后释放 ✓。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

# 单桶可接受的最大时间偏移（桶数）：超出则钳到窗口内，防止时钟异常导致内存无界。
MAX_LAG_BUCKETS = 10


@dataclass
class BucketStats:
    """一个桶的累计统计（与 `TradeBuffer` 字段一致，独立出来便于单测）。"""
    taker_buy_volume: Decimal = Decimal("0")
    taker_sell_volume: Decimal = Decimal("0")
    taker_buy_count: int = 0
    taker_sell_count: int = 0
    taker_buy_notional: Decimal = Decimal("0")
    taker_sell_notional: Decimal = Decimal("0")
    high_price: Optional[Decimal] = None
    low_price: Optional[Decimal] = None
    total_volume: Decimal = Decimal("0")
    total_notional: Decimal = Decimal("0")

    @property
    def vwap(self) -> Optional[Decimal]:
        if self.total_volume and self.total_volume > 0:
            return self.total_notional / self.total_volume
        return None

    def add(self, *, price: Decimal, size: Decimal, is_taker_buy: bool) -> None:
        notional = price * size
        if is_taker_buy:
            self.taker_buy_volume += size
            self.taker_buy_count += 1
            self.taker_buy_notional += notional
        else:
            self.taker_sell_volume += size
            self.taker_sell_count += 1
            self.taker_sell_notional += notional
        self.total_volume += size
        self.total_notional += notional
        if self.high_price is None or price > self.high_price:
            self.high_price = price
        if self.low_price is None or price < self.low_price:
            self.low_price = price


class TradeBucketStore:
    """按成交时间戳分桶的缓冲（见模块 docstring）。线程安全由调用方加锁保证。"""

    def __init__(self, window_ms: int = 15_000, grace_buckets: int = 2) -> None:
        self.window_ms = int(window_ms)
        self.grace_buckets = max(1, int(grace_buckets))
        self.open: Dict[str, BucketStats] = {}            # symbol → 当前桶缓冲
        self.open_label: Dict[str, int] = {}              # symbol → 当前桶标签
        self.closed: Dict[str, Dict[int, BucketStats]] = {}   # symbol → {label: 缓冲}

    # ── 基本换算 ──
    def label_of(self, ts_ms: int, *, now_ms: int) -> int:
        """成交时间戳 → 桶标签（钳制在 now 之前 MAX_LAG_BUCKETS 个桶内）。"""
        lab = (int(ts_ms) // self.window_ms) * self.window_ms
        cur = (int(now_ms) // self.window_ms) * self.window_ms
        lo = cur - MAX_LAG_BUCKETS * self.window_ms
        if lab < lo:
            lab = lo
        if lab > cur:
            lab = cur
        return lab

    def current_label(self, now_ms: int) -> int:
        return (int(now_ms) // self.window_ms) * self.window_ms

    # ── 写入 ──
    def add(self, symbol: str, *, price: Decimal, size: Decimal, is_taker_buy: bool,
            ts_ms: int, now_ms: int) -> int:
        """把一笔成交累加到它**自己时间戳**的桶；返回该桶标签。"""
        lab = self.label_of(ts_ms, now_ms=now_ms)
        cur = self.current_label(now_ms)
        if lab >= cur:
            buf = self.open.get(symbol)
            if buf is None:
                buf = BucketStats()
                self.open[symbol] = buf
            # 若上一个"当前桶"已经跨桶（说明 promote 尚未跑），先归位
            elif self.open_label.get(symbol) != lab:
                # [F192 2026-09-15 修 KeyError]
                # 这里必须**重新取/重建**缓冲：`_promote_symbol` 会把已结束的
                # "当前桶"移入 `closed` 并从 `self.open` 中 **pop 掉** ✓ ⇒ 紧接着的
                # `self.open[symbol]` 必然 KeyError ✗（实测：08:01 起每个币每轮都抛，
                # 5 小时 3612 次，且异常发生在 `_on_trade` 内 ⇒ **同一批成交里
                # 之后的成交全部丢失** ✗✗ —— 这是"链路无断点"的直接破口）。
                # 语义上也更正确：被 promote 走的是 `open_label < cur ≤ lab` 那个
                # **已结束**的桶 ✓，新的 `lab` 桶本来就该从空开始 ✓（两者不可能同标签，
                # 因为 promote 只搬运 `label < cur` 的桶 ✓）。
                self._promote_symbol(symbol, cur)
                buf = self.open.get(symbol)
                if buf is None:
                    buf = BucketStats()
                    self.open[symbol] = buf
            self.open_label[symbol] = lab
        else:
            buf = self.closed.setdefault(symbol, {}).setdefault(lab, BucketStats())
        buf.add(price=price, size=size, is_taker_buy=is_taker_buy)
        return lab

    def _promote_symbol(self, symbol: str, cur_label: int) -> None:
        """把"当前桶"缓冲移入已关闭集合（若它确实已结束）。"""
        buf = self.open.get(symbol)
        lab = self.open_label.get(symbol)
        if buf is None or lab is None:
            return
        if lab < cur_label:
            if buf.total_volume > 0:
                dst = self.closed.setdefault(symbol, {}).setdefault(lab, BucketStats())
                _merge(dst, buf)
            self.open.pop(symbol, None)
            self.open_label.pop(symbol, None)

    # ── 落库前取数 ──
    def ready(self, now_ms: int) -> List[Tuple[str, int, BucketStats]]:
        """返回**已结束**（可安全落库）的桶：先归位当前桶，再列出已关闭桶。

        只返回 `label < 当前桶标签` 的桶 ✓（未结束的桶绝不落库，避免半成品被
        做市判定读走 ✗）；有序输出便于测试与日志。
        """
        cur = self.current_label(now_ms)
        for sym in list(self.open):
            self._promote_symbol(sym, cur)
        out: List[Tuple[str, int, BucketStats]] = []
        for sym, buckets in self.closed.items():
            for lab in sorted(buckets):
                if lab < cur:
                    out.append((sym, lab, buckets[lab]))
        return out

    def prune(self, now_ms: int) -> int:
        """释放超过宽限期的已关闭桶（它们已被幂等写库多次）。返回释放数量。"""
        cur = self.current_label(now_ms)
        floor = cur - self.grace_buckets * self.window_ms
        n = 0
        for sym in list(self.closed):
            buckets = self.closed[sym]
            for lab in [x for x in buckets if x < floor]:
                buckets.pop(lab, None)
                n += 1
            if not buckets:
                self.closed.pop(sym, None)
        return n

    def symbols(self) -> List[str]:
        return sorted(set(list(self.open) + list(self.closed)))

    def open_volume(self, symbol: str) -> Decimal:
        return self.open[symbol].total_volume if symbol in self.open else Decimal("0")


def _merge(dst: BucketStats, src: BucketStats) -> None:
    dst.taker_buy_volume += src.taker_buy_volume
    dst.taker_sell_volume += src.taker_sell_volume
    dst.taker_buy_count += src.taker_buy_count
    dst.taker_sell_count += src.taker_sell_count
    dst.taker_buy_notional += src.taker_buy_notional
    dst.taker_sell_notional += src.taker_sell_notional
    dst.total_volume += src.total_volume
    dst.total_notional += src.total_notional
    if src.high_price is not None and (dst.high_price is None or src.high_price > dst.high_price):
        dst.high_price = src.high_price
    if src.low_price is not None and (dst.low_price is None or src.low_price < dst.low_price):
        dst.low_price = src.low_price
