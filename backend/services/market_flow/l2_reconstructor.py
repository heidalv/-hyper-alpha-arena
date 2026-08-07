"""L2 订单簿重建层（v6 阶段 2 第 7 项）。

背景（对齐 v6 计划 §2.2/§5.4 与实机现状）:
  - `market_flow/l2_orderbook_manager.py` 已做 L2 快照的**清洗/健康层**（gap 检测、
    ghost level 裁剪、crossed book 检测），但它是"旁路健康记录 + 原样返回"，
    不产出微观结构计算值。
  - `factor_engine/factors/derivatives/orderflow_crypto_factors.py` 的 CVD/OFI 因子
    已预留"真实数据优先，代理降级"钩子：K 线 DataFrame 一旦出现 taker_buy_volume
    列即自动切换到真实数据。
  - 本模块补齐中间层：把 L2 快照重建为标准多档簿帧 → 计算 OFI/CVD/深度指标 →
    按 K 线时间桶聚合 → 附加 taker_buy_volume / L2 深度列到 K 线 DataFrame，
    现有因子零改动自动升级为真实数据。

设计:
  - 纯 numpy/dataclasses，无重依赖、无网络调用（输入由调用方喂入）。
  - 与两种既有快照格式兼容：Hyperliquid l2Book `levels` 格式
    ({"levels": [[{"px","sz"},...], [...]]}) 与 market_data_hub L2Snapshot
    (bids/asks = [[price, size], ...])。
  - 线程安全（行情回调线程 + 因子消费线程并发）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# 注：v6 计划草案路径为 backend/services/market_data/l2_reconstructor.py，但
# 实机中 backend/services/market_data 是模块名（market_data.py），非包目录；
# 为不破坏现有 `from backend.services.market_data import ...` 导入，本模块落于
# market_flow 包（与 l2_orderbook_manager.py 同域）。

# 默认最大档位数（对齐 l2_orderbook_manager.DEFAULT_MAX_LEVELS 的 1000 口径）
DEFAULT_MAX_LEVELS = 200
# 深度指标默认档位
DEFAULT_DEPTH_LEVELS = 5


# ─────────────────────────── 数据结构 ───────────────────────────

@dataclass
class OrderBookFrame:
    """标准多档订单簿帧（price/size 均已 float 化、按价位排序）。"""
    exchange: str
    symbol: str
    bids: List[Tuple[float, float]] = field(default_factory=list)
    asks: List[Tuple[float, float]] = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    # ── 派生指标 ──
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    def mid_price(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return (self.best_bid() + self.best_ask()) / 2.0

    def spread_bps(self) -> float:
        """买卖价差（bp），无有效双边时返回 0。"""
        if not self.bids or not self.asks:
            return 0.0
        mid = self.mid_price()
        if mid <= 0:
            return 0.0
        return (self.best_ask() - self.best_bid()) / mid * 1e4

    def top_n(self, n: int = DEFAULT_DEPTH_LEVELS) -> "OrderBookFrame":
        """截取前 n 档（不影响原始帧）。"""
        return OrderBookFrame(
            exchange=self.exchange, symbol=self.symbol,
            bids=self.bids[:n], asks=self.asks[:n], ts=self.ts,
        )

    def notional_depth(self, n: int = DEFAULT_DEPTH_LEVELS) -> Tuple[float, float]:
        """前 n 档名义深度 (bid_notional, ask_notional)。"""
        bid = sum(px * sz for px, sz in self.bids[:n])
        ask = sum(px * sz for px, sz in self.asks[:n])
        return bid, ask

    def depth_imbalance(self, n: int = DEFAULT_DEPTH_LEVELS) -> float:
        """前 n 档深度失衡 [−1, 1]：(bid−ask)/(bid+ask)，>0 买盘占优。"""
        bid, ask = self.notional_depth(n)
        denom = bid + ask
        return (bid - ask) / denom if denom > 0 else 0.0


def _norm_levels(
    raw: Sequence[Any],
    is_hl_levels: bool,
    max_levels: int,
) -> List[Tuple[float, float]]:
    """归一化档位为 [(price, size)]：
    - Hyperliquid 格式: {"px": "123.4", "sz": "1.5"}
    - 通用格式: [price, size] 或 (price, size)
    """
    out: List[Tuple[float, float]] = []
    for row in raw[:max_levels]:
        try:
            if is_hl_levels:
                px = float(row["px"])
                sz = float(row["sz"])
            else:
                px = float(row[0])
                sz = float(row[1])
            if px > 0 and sz > 0:
                out.append((px, sz))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    return out


# ─────────────────────────── 重建器 ───────────────────────────

class L2Reconstructor:
    """L2 订单簿重建 + 插针防护 + OFI/CVD 计算。

    用法:
        rec = L2Reconstructor(max_price_jump_pct=2.0)   # 单例或实例均可
        frame = rec.ingest_hl("hyperliquid", "BTC", levels, ts=...)
        # 或
        frame = rec.ingest_book("hyperliquid", "BTC", bids, asks, ts=...)
        ofi = L2Reconstructor.compute_ofi(prev_frame, frame, levels=5)
    """

    def __init__(self, max_price_jump_pct: float = 0.0, max_levels: int = DEFAULT_MAX_LEVELS):
        self.max_price_jump_pct = float(max_price_jump_pct)
        self.max_levels = int(max_levels)
        self._frames: Dict[Tuple[str, str], OrderBookFrame] = {}
        self._lock = threading.Lock()
        self.jump_rejected: int = 0
        self.jump_rejected_detail: List[str] = []

    def ingest_hl(self, exchange: str, symbol: str, levels: Sequence[Any],
                  ts: Optional[float] = None) -> OrderBookFrame:
        """Hyperliquid l2Book 格式: levels = [bids, asks]，元素 {"px","sz"}。"""
        bids_raw, asks_raw = levels[0] or [], levels[1] or []
        return self.ingest_book(
            exchange, symbol,
            _norm_levels(bids_raw, True, self.max_levels),
            _norm_levels(asks_raw, True, self.max_levels),
            ts=ts,
        )

    def ingest_book(self, exchange: str, symbol: str,
                    bids: Sequence[Any], asks: Sequence[Any],
                    ts: Optional[float] = None) -> OrderBookFrame:
        """通用格式: bids/asks = [[price, size], ...]（与 hub L2Snapshot 一致）。"""
        if ts is None:
            ts = time.time()
        bid_n = _norm_levels(bids, False, self.max_levels)
        ask_n = _norm_levels(asks, False, self.max_levels)
        frame = OrderBookFrame(exchange=exchange, symbol=symbol,
                               bids=bid_n, asks=ask_n, ts=float(ts))
        with self._lock:
            prev = self._frames.get((exchange, symbol))
            if prev and self._is_jump(prev, frame):
                self.jump_rejected += 1
                if len(self.jump_rejected_detail) < 20:
                    self.jump_rejected_detail.append(
                        f"{exchange}:{symbol} mid {prev.mid_price():.4f}->{frame.mid_price():.4f}"
                    )
                return prev  # 拒绝本次异常跳变，返回上一帧
            self._frames[(exchange, symbol)] = frame
        return frame

    def latest(self, exchange: str, symbol: str) -> Optional[OrderBookFrame]:
        with self._lock:
            return self._frames.get((exchange, symbol))

    def _is_jump(self, prev: OrderBookFrame, cur: OrderBookFrame) -> bool:
        if self.max_price_jump_pct <= 0:
            return False
        p_mid = prev.mid_price()
        c_mid = cur.mid_price()
        if p_mid <= 0 or c_mid <= 0:
            return False
        pct = abs(c_mid - p_mid) / p_mid * 100.0
        return pct > self.max_price_jump_pct

    # ── OFI / CVD ──

    @staticmethod
    def compute_ofi(prev: Optional[OrderBookFrame], cur: OrderBookFrame,
                    levels: int = DEFAULT_DEPTH_LEVELS) -> float:
        """标准 OFI（Cont-Kukanov-Stoikov 简化实现，逐档失衡增量求和）:

            OFI_t = Σ_p [ Δbid_size_t(p) − Δask_size_t(p) ]

        其中 Δx(p) = x_t(p) − x_{t−1}(p)，价格档 p 取前后帧在该价位上的数量差。
        无前一帧时返回 0（无法计算增量）。
        """
        if prev is None:
            return 0.0
        prev_bid = dict(prev.bids[:levels])
        prev_ask = dict(prev.asks[:levels])
        cur_bid = dict(cur.bids[:levels])
        cur_ask = dict(cur.asks[:levels])
        prices = sorted(
            set(prev_bid) | set(prev_ask) | set(cur_bid) | set(cur_ask)
        )[:levels * 2]
        ofi = 0.0
        for p in prices:
            db = cur_bid.get(p, 0.0) - prev_bid.get(p, 0.0)
            da = cur_ask.get(p, 0.0) - prev_ask.get(p, 0.0)
            ofi += db - da
        return float(ofi)

    @staticmethod
    def compute_cvd(trades: Sequence[Tuple[float, float, str]]) -> float:
        """CVD（累计成交量差）: Σ qty × sign(side)，side ∈ {buy, sell}。

        真实逐笔 taker 方向数据（由行情采集喂入）；trades 为空返回 0。
        """
        cvd = 0.0
        for price, qty, side in trades:
            s = side.lower()
            if s in ("buy", "b", "1", "taker_buy"):
                cvd += float(qty)
            elif s in ("sell", "s", "-1", "taker_sell"):
                cvd -= float(qty)
        return float(cvd)


# ─────────────────────────── K 线聚合 ───────────────────────────

class KlineDepthAggregator:
    """把连续帧/逐笔序列按 K 线时间桶聚合，产出补列数据。

    输出列（与 orderflow_crypto_factors 的自动切换钩子对齐）:
        taker_buy_volume : 桶内真实 taker 买入量（无逐笔数据时 NaN → 因子走代理）
        bid_depth_top5   : 桶内末帧前 5 档买盘名义深度
        ask_depth_top5   : 桶内末帧前 5 档卖盘名义深度
        depth_imbalance  : 桶内末帧深度失衡 [−1,1]
        spread_bps       : 桶内 spread 均值（bp）
    """

    COLUMNS = ["taker_buy_volume", "bid_depth_top5", "ask_depth_top5",
               "depth_imbalance", "spread_bps"]

    def __init__(self, bar_seconds: int = 300):
        self.bar_seconds = int(bar_seconds)

    def aggregate(self, frames: Sequence[OrderBookFrame],
                  trades: Optional[Sequence[Tuple[float, float, float, str]]] = None,
                  start_ts: Optional[float] = None,
                  end_ts: Optional[float] = None) -> Dict[int, Dict[str, float]]:
        """frames 按时间桶聚合 → {bar_start_ts: {col: value}}。

        trades 为逐笔流，格式 (ts, price, qty, side)；无逐笔时 taker_buy_volume 为
        NaN（因子侧自动走代理）。start_ts/end_ts 缺省取 frames 的 min/max。
        """
        if not frames:
            return {}
        if start_ts is None:
            start_ts = min(f.ts for f in frames)
        if end_ts is None:
            end_ts = max(f.ts for f in frames)
        # 桶内成交可能晚于最后一帧（末帧后、桶关闭前的成交），扩展窗口覆盖 trades
        if trades:
            max_t = max((t[0] for t in trades if isinstance(t[0], (int, float))), default=end_ts)
            end_ts = max(float(end_ts), float(max_t))
        buckets: Dict[int, List[OrderBookFrame]] = {}
        for f in frames:
            if f.ts < start_ts or f.ts > end_ts:
                continue
            b = int(f.ts // self.bar_seconds) * self.bar_seconds
            buckets.setdefault(b, []).append(f)

        taker_by_bucket: Dict[int, float] = {}
        if trades:
            for t_ts, _price, qty, side in trades:
                if not isinstance(t_ts, (int, float)) or t_ts < start_ts or t_ts > end_ts:
                    continue
                if side.lower() not in ("buy", "b", "1", "taker_buy"):
                    continue
                b = int(t_ts // self.bar_seconds) * self.bar_seconds
                taker_by_bucket[b] = taker_by_bucket.get(b, 0.0) + float(qty)

        out: Dict[int, Dict[str, float]] = {}
        for b, fs in buckets.items():
            last = fs[-1].top_n(DEFAULT_DEPTH_LEVELS)
            bid_d, ask_d = last.notional_depth()
            spread_avg = float(np.mean([f.spread_bps() for f in fs])) if fs else 0.0
            out[b] = {
                "taker_buy_volume": taker_by_bucket.get(b, np.nan),
                "bid_depth_top5": bid_d,
                "ask_depth_top5": ask_d,
                "depth_imbalance": last.depth_imbalance(),
                "spread_bps": spread_avg,
            }
        return out

    def attach_depth_columns(self, df: pd.DataFrame, frames: Sequence[OrderBookFrame],
                             trades: Optional[Sequence[Tuple[float, float, float, str]]] = None,
                             ts_col: str = "timestamp") -> pd.DataFrame:
        """把聚合结果按时间桶附加到 K 线 DataFrame（新列不存在时创建）。"""
        if df is None or df.empty or not frames:
            for c in self.COLUMNS:
                if c not in df.columns:
                    df[c] = np.nan
            return df

        ts = df[ts_col].astype(float)
        agg = self.aggregate(frames, trades,
                             start_ts=float(ts.min()), end_ts=float(ts.max()))
        for c in self.COLUMNS:
            if c not in df.columns:
                df[c] = np.nan
        for i, t in enumerate(ts):
            b = int(t // self.bar_seconds) * self.bar_seconds
            row = agg.get(b)
            if row is None:
                continue
            for c in self.COLUMNS:
                df.at[df.index[i], c] = row[c]
        return df


# 进程内默认实例（供行情回调直接 ingest，因子/聚合按需自建实例）
default_reconstructor = L2Reconstructor()
