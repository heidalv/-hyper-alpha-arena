"""tick 级行情数据层 —— 替代 15s 桶（`market_orderbook_snapshots` + `market_trades_aggregated`）

## 为什么要换

旧引擎（`runner.py`）的成交判定读两张**粗粒度**表：
  · `market_orderbook_snapshots` —— 15s 网格，但**真实上游是 30s REST 轮询**，
    且空桶不落行 ⇒ 网格填充率仅 **47.5%**（`portfolio_replay.py:316-317` 自述）；
  · `market_trades_aggregated` —— **15s 成交桶**，只有 low/high/taker_buy/taker_sell 聚合量。

于是成交判定退化成：
    hit_buy = (seg_low < bid*(1-pen)) and (seg_taker_sell > 0)

这在**挂宽 30bp**（盘口外约 20 倍）时无害——那种价位本来就不争。但一旦按设计挂到
**盘口**（Aster 半价差中位 0.738bp），15 秒内价格往返穿越盘口是常态，该判定的两个
条件既会**漏判**（价格触及但 seg_low 未越过穿透阈值）也会**误判**（价格穿越后回归，
我却按穿越时刻的旧 mid 记账）。

## 本模块提供什么

`TickFeed` —— 亚秒级盘口 + 逐笔成交，两个实现：
  · `DbTickFeed`       —— 从 PG 读（回测/回放用）
  · `InMemoryTickFeed` —— 由调用方塞数组（实盘/单元测试用）

数据源（实测粒度）：
  · `asterdex_book_ticker` —— top-of-book，**p50 36ms**，1.027 亿行，覆盖 3.9 天，32 币
  · `asterdex_trades`      —— 逐笔含主动方，**p50 1.5s**，191.7 万行，覆盖 3.9 天，32 币

单位约定（务必记住，踩过）：
  `asterdex_book_ticker.event_ts_ms` / `asterdex_trades.event_ts_ms` = epoch **毫秒**
  （而 `crypto_klines.timestamp` 是 epoch **秒**）
"""
from __future__ import annotations

import bisect
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

MARKET_DB = "alpha_market"


def _market_dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    return f"{head}/{MARKET_DB}"


@dataclass(frozen=True)
class QuoteLevel:
    """某一时刻的盘口（一档）。"""
    ts_ms: int
    bid: float
    bid_qty: float
    ask: float
    ask_qty: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class TickFeed:
    """亚秒级行情接口。所有时间参数均为 epoch **毫秒**。"""

    symbol: str

    # ---- 必须实现 ----
    def quote_at(self, ts_ms: int) -> Optional[QuoteLevel]:
        raise NotImplementedError

    def trades_in(self, t0_ms: int, t1_ms: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """返回 `(ts, price, qty, is_buyer_maker)` 在 [t0, t1) 的切片。

        `is_buyer_maker=True` 表示**主动卖**（打 bid）。
        """
        raise NotImplementedError

    def trade_ts(self) -> np.ndarray:
        raise NotImplementedError

    def span_ms(self) -> Tuple[int, int]:
        raise NotImplementedError

    # ---- 派生：各档位的前视时间 ----
    def mid_at(self, ts_ms: int) -> Optional[float]:
        q = self.quote_at(ts_ms)
        return q.mid if q else None


class InMemoryTickFeed(TickFeed):
    """内存实现（实盘 / 测试）。数组须按时间升序。"""

    def __init__(self, symbol: str, bts, bid, bq, ask, aq, tts, tpx, tqt, tbm):
        self.symbol = symbol
        self._bts = np.asarray(bts, dtype=np.int64)
        self._bid = np.asarray(bid, dtype=np.float64)
        self._bq = np.asarray(bq, dtype=np.float64)
        self._ask = np.asarray(ask, dtype=np.float64)
        self._aq = np.asarray(aq, dtype=np.float64)
        self._tts = np.asarray(tts, dtype=np.int64)
        self._tpx = np.asarray(tpx, dtype=np.float64)
        self._tqt = np.asarray(tqt, dtype=np.float64)
        self._tbm = np.asarray(tbm, dtype=bool)
        # 按价格分桶的对手方主动成交量（队列消耗用）
        self._by_px: Dict[str, Dict[float, Tuple[List[int], np.ndarray]]] = {}
        self._build_px_index()

    def _build_px_index(self) -> None:
        from collections import defaultdict
        for tag, want_bm in (("bid", True), ("ask", False)):
            d = defaultdict(list)
            for k in range(len(self._tts)):
                if bool(self._tbm[k]) is want_bm:
                    d[round(float(self._tpx[k]), 12)].append((int(self._tts[k]), float(self._tqt[k])))
            built = {}
            for px, arr in d.items():
                arr.sort()
                built[px] = ([a[0] for a in arr], np.cumsum([a[1] for a in arr]))
            self._by_px[tag] = built

    def quote_at(self, ts_ms: int) -> Optional[QuoteLevel]:
        i = bisect.bisect_right(self._bts, int(ts_ms)) - 1
        if i < 0:
            return None
        return QuoteLevel(int(self._bts[i]), float(self._bid[i]), float(self._bq[i]),
                          float(self._ask[i]), float(self._aq[i]))

    def trades_in(self, t0_ms: int, t1_ms: int):
        i0 = bisect.bisect_left(self._tts, t0_ms)
        i1 = bisect.bisect_left(self._tts, t1_ms)
        return (self._tts[i0:i1], self._tpx[i0:i1], self._tqt[i0:i1], self._tbm[i0:i1])

    def trade_ts(self):
        return self._tts

    def span_ms(self):
        return int(self._bts[0]), int(self._bts[-1])

    # 队列消耗查询：在 [t0, inf) 内，某价位上对手方累计成交量超过 q_ahead 的时刻
    def consume_time(self, side: str, price: float, q_ahead: float, t_from_ms: int) -> Optional[int]:
        """返回该价位对手方**累计**成交量首次超过 `q_ahead` 的时间戳；无则 None。

        side="bid"：我等在买盘，消耗我的是**主动卖**（is_buyer_maker=True）。
        """
        entry = self._by_px[side].get(round(price, 12))
        if entry is None:
            return None
        ts_arr, cum = entry
        k = bisect.bisect_right(ts_arr, int(t_from_ms))
        if k >= len(ts_arr):
            return None
        base = float(cum[k - 1]) if k > 0 else 0.0
        j = bisect.bisect_right(cum, base + max(0.0, float(q_ahead)), lo=k)
        if j >= len(ts_arr):
            return None
        return int(ts_arr[j])

    # 是否有成交在 [t0,t1) 内以 >= / <= 某价成交（平仓腿判定）
    def trade_cross_time(self, side: str, price: float, t_from_ms: int, t_until_ms: int) -> Optional[int]:
        """side="ask"：我的卖单挂在 `price`，主动买价 >= price 即成交。"""
        entry = self._by_px[side].get(round(price, 12))
        if entry is None:
            return None
        ts_arr, _ = entry
        k = bisect.bisect_right(ts_arr, int(t_from_ms))
        if k < len(ts_arr) and ts_arr[k] <= int(t_until_ms):
            return int(ts_arr[k])
        return None

    def recent_volume(self, side: str, price: float, t_ms: int, lookback_ms: int = 10_000) -> float:
        """近 `lookback_ms` 内该价位对手方成交量（估计"排在我前面"的量）。"""
        entry = self._by_px[side].get(round(price, 12))
        if entry is None:
            return 0.0
        ts_arr, cum = entry
        k = bisect.bisect_right(ts_arr, int(t_ms))
        j = bisect.bisect_left(ts_arr, int(t_ms) - lookback_ms)
        if k <= 0:
            return 0.0
        return float(cum[k - 1]) - (float(cum[j - 1]) if j > 0 else 0.0)


class DbTickFeed(InMemoryTickFeed):
    """从 PG 载入。覆盖 3.9 天（两个表的最早 ts 决定）。"""

    @classmethod
    def load(cls, symbol: str, t0_ms: int, t1_ms: int, *,
             conn=None) -> "DbTickFeed":
        import psycopg2
        own = conn is None
        if own:
            conn = psycopg2.connect(_market_dsn())
            conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute(
                "select event_ts_ms,bid_px,bid_qty,ask_px,ask_qty from asterdex_book_ticker "
                "where symbol=%s and event_ts_ms >= %s and event_ts_ms < %s order by event_ts_ms",
                (symbol, t0_ms, t1_ms))
            b = cur.fetchall()
            cur.execute(
                "select event_ts_ms,price,qty,is_buyer_maker from asterdex_trades "
                "where symbol=%s and event_ts_ms >= %s and event_ts_ms < %s order by event_ts_ms",
                (symbol, t0_ms, t1_ms))
            t = cur.fetchall()
        finally:
            if own:
                conn.close()
        if not b:
            raise ValueError(f"{symbol}: 盘口数据为空（{t0_ms}~{t1_ms}）")
        bts = [r[0] for r in b]
        bid = [r[1] for r in b]
        bq = [r[2] for r in b]
        ask = [r[3] for r in b]
        aq = [r[4] for r in b]
        tts = [r[0] for r in t]
        tpx = [r[1] for r in t]
        tqt = [r[2] for r in t]
        tbm = [r[3] for r in t]
        return cls(symbol, bts, bid, bq, ask, aq, tts, tpx, tqt, tbm)

    @staticmethod
    def coverage(conn=None) -> dict:
        """两个表的可用窗口与币种（用于决定回测区间）。"""
        import psycopg2
        own = conn is None
        if own:
            conn = psycopg2.connect(_market_dsn())
            conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute("select min(event_ts_ms), max(event_ts_ms) from asterdex_book_ticker")
            blo, bhi = cur.fetchone()
            cur.execute("select min(event_ts_ms), max(event_ts_ms) from asterdex_trades")
            tlo, thi = cur.fetchone()
            return {"book": [int(blo), int(bhi)], "trades": [int(tlo), int(thi)],
                    "usable": [max(int(blo), int(tlo)), min(int(bhi), int(thi))]}
        finally:
            if own:
                conn.close()
