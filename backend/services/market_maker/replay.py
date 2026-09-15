# -*- coding: utf-8 -*-
"""[F59] L1 做市车道的影子期回放器（模拟账户直跑，真实行情 + 真实费率）。

用途：
  1. 用**真实盘口快照 + 区间成交明细**驱动 `market_maker.core` 的报价/库存/成交判定；
  2. 产出影子期达标报告（每笔净期望、六维归因、分折稳健性）；
  3. 把成交写入 `lane_ledger`、把 edge 写入 `lane_registry`（paper 模式）。

与 F52 离线模拟**同口径**，保证「离线结论 = 影子结论」可比。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "XRP", "SOL", "DOGE"]
DEFAULT_VENUE = "asterdex"
DEFAULT_EQUITY = float(os.getenv("F59_EQUITY", "5000"))
FILL_NOTIONAL = float(os.getenv("F59_FILL_NOTIONAL", "100"))   # 每次挂单名义（美元）
HOLD_SNAPSHOTS = int(os.getenv("F59_HOLD", "10"))              # 逆选择观察窗（快照数）
MAX_ONE_SIDE_SNAPSHOTS = int(os.getenv("F59_MAX_ONE_SIDE_SNAPSHOTS", "8"))  # ≈2 分钟
TAKER_FEE_BP = float(os.getenv("F59_TAKER_FEE_BP", "4"))       # 主动平仓的 taker 成本
QUEUE_SHARE = float(os.getenv("F59_QUEUE_SHARE", "0.30"))      # 队列份额：我们排在既有做市商之后
MIN_FILL_NOTIONAL = float(os.getenv("F59_MIN_FILL_NOTIONAL", "10"))  # 低于此不记成交
PENETRATION_BP = float(os.getenv("F59_PENETRATION_BP", "0.0"))  # 需要「穿过」挂单多少 bp 才成交
# 相邻快照间隔超过此值视为数据缺口（采集器停更）→ 跳过该区间，避免幻影成交
MAX_GAP_MS = int(os.getenv("F59_MAX_GAP_MS", "120000"))


@dataclass
class SymbolResult:
    symbol: str
    snapshots: int = 0
    fills: int = 0
    spread_usd: float = 0.0
    price_usd: float = 0.0
    fee_usd: float = 0.0
    net_usd: float = 0.0
    notional: float = 0.0
    folds: List[Dict[str, float]] = field(default_factory=list)
    skipped: Dict[str, int] = field(default_factory=dict)
    flattens: int = 0
    flatten_usd: float = 0.0
    flatten_price_usd: float = 0.0
    flatten_notional: float = 0.0
    max_one_side_usd: float = 0.0
    avg_hold_snapshots: float = 0.0
    window_days: float = 0.0
    covered_days: float = 0.0     # 有效覆盖时长（剔除断流缺口）
    vol_baseline_bp: float = 0.0  # 该窗口的已实现波动中位数（F71b 基准）
    fill_log: List[Dict[str, Any]] = field(default_factory=list)  # 逐笔日志（组合分析用）

    @property
    def net_bp(self) -> float:
        return (self.net_usd / self.notional * 1e4) if self.notional > 0 else 0.0

    @property
    def spread_bp(self) -> float:
        return (self.spread_usd / self.notional * 1e4) if self.notional > 0 else 0.0

    @property
    def price_bp(self) -> float:
        return (self.price_usd / self.notional * 1e4) if self.notional > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "snapshots": self.snapshots, "fills": self.fills,
            "notional": round(self.notional, 2),
            "spread_usd": round(self.spread_usd, 4),
            "price_usd": round(self.price_usd, 4),
            "fee_usd": round(self.fee_usd, 4),
            "net_usd": round(self.net_usd, 4),
            "spread_bp": round(self.spread_bp, 3),
            "price_bp": round(self.price_bp, 3),
            "net_bp": round(self.net_bp, 3),
            "folds": self.folds, "skipped": self.skipped,
            "flattens": self.flattens, "flatten_usd": round(self.flatten_usd, 4),
            # 平仓的**价格维度**成本（bp）——与影子期同口径，用于对比
            "flatten_price_bp": (round(self.flatten_price_usd / self.flatten_notional * 1e4, 4)
                                 if self.flatten_notional > 0 else None),
            "flatten_notional": round(self.flatten_notional, 2),
            "flatten_share": (round(self.flattens / self.fills, 4)
                              if self.fills else None),
            "max_one_side_usd": round(self.max_one_side_usd, 2),
            "avg_hold_snapshots": round(self.avg_hold_snapshots, 2),
            "window_days": round(self.window_days, 3),
            "covered_days": round(self.covered_days, 3),
            "vol_baseline_bp": round(self.vol_baseline_bp, 4),
        }


def _load_series(symbol: str, venue: str, start_ts: Optional[int] = None,
                 *, with_created: bool = False):
    """读取盘口快照与区间成交（毫秒时间戳）。

    [F107] `with_created=True` 时额外返回第 9 个数组 `tmk` = 每个成交桶的
    **落库时刻**（`created_at`，毫秒）。为什么必要：asterdex 的成交桶是**按落库时刻
    分桶**（`floor(flush_time/15s)`）且**空桶不落行** ⇒ 一个桶在它自己的标签时刻
    **可能还不存在**。实盘 tick 只能看到"当时已落库"的桶；回放若不做这个可见性过滤，
    就会用到实盘当刻还看不到的数据 ⇒ 系统性偏乐观。`tmk` 让回放与实盘逐刻对齐。
    """
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import MarketSessionLocal

    with system_identity():
        with MarketSessionLocal() as db:
            ob = db.execute(text(
                "SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots"
                " WHERE exchange=:e AND symbol=:s AND best_bid>0 AND best_ask>best_bid"
                " ORDER BY timestamp ASC"
            ), {"e": venue, "s": symbol}).mappings().all()
            tr = db.execute(text(
                "SELECT timestamp, low_price, high_price, taker_sell_volume, taker_buy_volume,"
                " created_at"
                " FROM market_trades_aggregated WHERE exchange=:e AND symbol=:s"
                " ORDER BY timestamp ASC"
            ), {"e": venue, "s": symbol}).mappings().all()

    ots = np.array([int(r["timestamp"]) for r in ob], dtype=np.int64)
    bb = np.array([float(r["best_bid"]) for r in ob])
    ba = np.array([float(r["best_ask"]) for r in ob])
    tts = np.array([int(r["timestamp"]) for r in tr], dtype=np.int64)
    lo = np.array([float(r["low_price"] or 0) for r in tr])
    hi = np.array([float(r["high_price"] or 0) for r in tr])
    sv = np.array([float(r["taker_sell_volume"] or 0) for r in tr])
    bv = np.array([float(r["taker_buy_volume"] or 0) for r in tr])
    if start_ts:
        m = ots >= int(start_ts)
        ots, bb, ba = ots[m], bb[m], ba[m]
    if with_created:
        # [F107] `created_at` 是 **naive** 时间戳，且列里存的是"本地墙钟"（实测与桶标签
        # 的差 = 1~13s ✓）。直接用 `EXTRACT(EPOCH ...)` 会被当成 UTC ⇒ 整体偏 **8 小时**
        # （实测 28807s ✗，会把所有桶都判成"当时还没落库"⇒ 回放 0 成交）。这里统一：
        # 先按本地时区取 epoch；若与标签相差超过 1 小时（说明列语义是 UTC-naive），
        # 再减去本地偏移。异常行（NULL）按"落后一个桶"保守处理。
        _off_ms = int((datetime.now().astimezone().utcoffset() or timedelta(0))
                      .total_seconds() * 1000)
        _tmk: List[int] = []
        for r in tr:
            _lab = int(r["timestamp"])
            _ca = r["created_at"]
            if _ca is None:
                _tmk.append(_lab + 15000)
                continue
            try:
                _v = int(_ca.timestamp() * 1000)
            except Exception:
                _tmk.append(_lab + 15000)
                continue
            if _v - _lab > 3_600_000:
                _v -= _off_ms
            _tmk.append(_v)
        tmk = np.array(_tmk, dtype=np.int64)
        return ots, bb, ba, tts, lo, hi, sv, bv, tmk
    return ots, bb, ba, tts, lo, hi, sv, bv


def replay_symbol(
    symbol: str,
    *,
    venue: str = DEFAULT_VENUE,
    equity: float = DEFAULT_EQUITY,
    params=None,
    limits=None,
    start_ts: Optional[int] = None,
    n_folds: int = 6,
    record: bool = False,
    series: Optional[Tuple[Any, ...]] = None,
    fill_notional: Optional[float] = None,
    return_fills: bool = False,
) -> SymbolResult:
    """回放单个币的做市影子期。

    Args:
        series: 预先加载的 `_load_series()` 结果（参数扫描时复用，避免反复读库）。
        fill_notional: 每笔挂单名义（默认取模块常量）。
        return_fills: 是否记录逐笔日志（组合级回撤/对冲分析用）。
    """
    fill_notional = float(fill_notional if fill_notional is not None else FILL_NOTIONAL)
    from backend.services.market_maker.core import (
        InventoryBook, QuoteParams, LaneRiskLimits, check_side_allowed,
        compute_quote, fill_side, sigma_norm_from_ranges,
        realized_vol_bp, should_stop_loss, trend_blocked_side,
    )

    params = params or QuoteParams()
    # 回放的快照间隔约 14s，超时平仓阈值按「快照数」折算，避免用实盘 300s 却只有
    # 30 天历史（≈4.2 快照/分钟）导致库存长期挂账。
    if limits is None:
        limits = LaneRiskLimits(max_one_side_seconds=MAX_ONE_SIDE_SNAPSHOTS * 14.0)
    res = SymbolResult(symbol=symbol)

    ots, bb, ba, tts, lo, hi, sv, bv = series if series is not None else _load_series(symbol, venue, start_ts)
    n = len(ots)
    if n < 100 or len(tts) < 50:
        res.skipped["no_data"] = 1
        return res
    res.snapshots = n
    res.window_days = round((int(ots[-1]) - int(ots[0])) / 86400_000.0, 3) if n >= 2 else 0.0
    # 有效覆盖时长：只累加「相邻快照间隔 < 5 分钟」的部分，剔除断流缺口。
    # 否则 22 天断流会把成交率分母虚增 3.5 倍（实测踩过）。
    if n >= 2:
        d = np.diff(ots)
        res.covered_days = round(float(d[d < 300_000].sum()) / 86400_000.0, 3)

    book = InventoryBook()
    limit_notional = equity * limits.max_net_directional_ratio
    hold_snaps = 0
    cycles = 0
    prev_flat = True
    fold_buckets: List[List[float]] = [[] for _ in range(n_folds)]
    fold_edges = np.linspace(0, n, n_folds + 1).astype(int)
    mid_hist: List[float] = []

    # [F74] 两遍法：第一遍扫全部中价，算「已实现波动中位数」作为基准。
    # 挂宽随波动缩放：w = w_base × (1 + k_vol × (vol/vol_baseline − 1))。
    # 低波动时退化为 w_base（已验证的 +2.5bp 配置）；高波动时自动加宽。
    _mids: List[float] = []
    for i in range(n):
        _m = float((bb[i] + ba[i]) / 2)
        if _m > 0:
            _mids.append(_m)
    vol_baseline = 0.0
    if len(_mids) >= 22:
        _vs = [realized_vol_bp(_mids[j - 20:j + 1], 20)
               for j in range(20, len(_mids))]
        _vs = [v for v in _vs if v > 0]
        if _vs:
            vol_baseline = float(np.median(_vs))
    res.vol_baseline_bp = round(vol_baseline, 4)

    def _flatten(book, symbol, side, qty, px, mid, now_ts, i, res, fold_buckets,
                 fold_edges, n_folds, record, ots, reason: str = "") -> None:
        """平仓并记账（超时/止损共用）。"""
        d = book.apply_fill(symbol=symbol, side=side, qty=qty, fill_px=px,
                            mid_px=mid, fee_rate=TAKER_FEE_BP / 1e4, now_ts=now_ts)
        res.flattens += 1
        res.fills += 1
        res.notional += d["notional"]
        res.spread_usd += d["spread_usd"]
        res.price_usd += d["price_usd"]
        res.fee_usd += d["fee_usd"]
        res.flatten_usd += d["net_usd"]
        res.flatten_price_usd += d["price_usd"]
        res.flatten_notional += d["notional"]
        if return_fills:
            res.fill_log.append({
                "ts_ms": int(ots[i]), "symbol": symbol, "side": side,
                "notional": round(float(d["notional"]), 4),
                "net_usd": round(float(d["net_usd"]), 6),
                "net_bp": round(float(d["net_usd"]) / max(1e-9, d["notional"]) * 1e4, 4),
                "flatten": True,
                "net_position_usd": round(float(book.notional(symbol, mid)), 4),
                "mid": mid,
            })
        f = int(np.searchsorted(fold_edges, i, "right")) - 1
        if 0 <= f < n_folds:
            fold_buckets[f].append(d["net_usd"] / max(1e-9, d["notional"]) * 1e4)
        if record:
            _record_fill(symbol=symbol, side=side, qty=qty, fill_px=px,
                         mid_px=mid, fee_rate=TAKER_FEE_BP / 1e4,
                         notional=d["notional"], ts_ms=ots[i],
                         meta={"reason": reason} if reason else None)

    for i in range(n - 1):
        mid = float((bb[i] + ba[i]) / 2)
        if mid <= 0:
            continue
        now_ts = ots[i] / 1000.0
        mid_hist.append(mid)
        if len(mid_hist) > 240:
            mid_hist.pop(0)
        # [F74] 波动信号：当前已实现波动相对基准的倍数（低波动≈0 → w 退化为 w_base）
        vol_cur = realized_vol_bp(mid_hist, limits.vol_window)
        sigma = (max(0.0, vol_cur / vol_baseline - 1.0)
                 if vol_baseline > 0 and limits.vol_pause_mult >= 0 else 0.0)
        # 硬暂停：波动超过基准的 (1 + mult) 倍 → 该币本快照不报价
        if (limits.vol_pause_mult > 0 and vol_baseline > 0
                and vol_cur > vol_baseline * (1.0 + limits.vol_pause_mult)):
            res.skipped["vol_regime"] = res.skipped.get("vol_regime", 0) + 1
            book.positions.pop(symbol, None)
            continue

        # 库存统计（持有快照数 / 开仓轮次）
        if abs(book.qty(symbol)) > 1e-12:
            hold_snaps += 1
        elif prev_flat is False:
            cycles += 1
        prev_flat = abs(book.qty(symbol)) <= 1e-12

        # ① 平仓：止损优先（浮亏超阈值），其次超时
        q_sym = book.qty(symbol)
        if abs(q_sym) > 1e-12:
            half_spread = (float(ba[i]) - float(bb[i])) / 2.0
            pos = book.positions.get(symbol)
            stop = should_stop_loss(q_sym, getattr(pos, "avg_mid", 0.0) or 0.0, mid,
                                    limits.stop_loss_bp)
            timeout = book.holding_seconds(symbol, now_ts) > limits.max_one_side_seconds
            if stop or timeout:
                if q_sym > 0:
                    flat_side, flat_px = "sell", mid - half_spread   # 砸 bid
                else:
                    flat_side, flat_px = "buy", mid + half_spread    # 吃 ask
                _flatten(book, symbol, flat_side, abs(q_sym), flat_px, mid, now_ts, i,
                         res, fold_buckets, fold_edges, n_folds, record, ots,
                         reason="stop_loss" if stop else "timeout")

        # ② 报价（库存偏斜 + [F204] 趋势反向偏斜）
        inv_ratio = book.inv_ratio(symbol, mid, limit_notional)
        from backend.services.market_maker.core import trend_move_bp as _tmb
        _trend_bp = _tmb(mid_hist, int(getattr(params, "trend_skew_lookback", 60) or 60))
        q = compute_quote(symbol=symbol, mid=mid, sigma_norm=sigma,
                          inv_ratio=inv_ratio, trend_bp=_trend_bp, params=params)
        if q is None:
            res.skipped["no_quote"] = res.skipped.get("no_quote", 0) + 1
            continue

        # ③ 单侧许可：减仓腿永远可挂，加仓腿受敞口约束
        marks = {symbol: mid}
        allow_buy, why_buy = check_side_allowed(
            symbol=symbol, side="buy", book=book, marks=marks, equity=equity,
            add_notional=fill_notional, limits=limits, now_ts=now_ts, sigma_norm=0.0)
        allow_sell, why_sell = check_side_allowed(
            symbol=symbol, side="sell", book=book, marks=marks, equity=equity,
            add_notional=fill_notional, limits=limits, now_ts=now_ts, sigma_norm=0.0)
        # [F71] 趋势闸门：单边行情里禁止逆势侧（下跌禁买、上涨禁卖）
        blocked = trend_blocked_side(mid_hist, limits.trend_pause_bp,
                                     limits.trend_lookback)
        if blocked == "buy" and allow_buy:
            allow_buy, why_buy = False, "trend_down"
        elif blocked == "sell" and allow_sell:
            allow_sell, why_sell = False, "trend_up"
        if not allow_buy and not allow_sell:
            key = (why_buy or why_sell or "blocked").split("(")[0]
            res.skipped[key] = res.skipped.get(key, 0) + 1
            continue

        # ④ 区间成交
        # 缺口保护：相邻快照间隔超过 2 分钟说明数据不连续（如采集器停更 22 天）。
        # 若不跳过，会拿「缺口前的挂单」去撞「缺口后的成交区间」→ 幻影成交
        # （实测把同一配置的净期望从 +2.42bp 拉到 +0.34bp）。
        if int(ots[i + 1]) - int(ots[i]) > MAX_GAP_MS:
            book.positions.pop(symbol, None)     # 丢弃缺口前的库存与挂单语义
            res.skipped["data_gap"] = res.skipped.get("data_gap", 0) + 1
            continue
        j0 = int(np.searchsorted(tts, ots[i], "left"))
        j1 = int(np.searchsorted(tts, ots[i + 1], "right"))
        if j1 <= j0:
            continue
        seg_low = float(lo[j0:j1].min())
        seg_high = float(hi[j0:j1].max())
        seg_sell = float(sv[j0:j1].sum())
        seg_buy = float(bv[j0:j1].sum())

        side = fill_side(bid=q.bid, ask=q.ask, seg_low=seg_low, seg_high=seg_high,
                         seg_taker_sell=seg_sell, seg_taker_buy=seg_buy,
                         penetration_bp=PENETRATION_BP)
        if side is None:
            continue

        qty = fill_notional / mid
        legs: List[Tuple[str, float, float]] = []
        # 队列份额约束：我们排在既有做市商之后，只能吃到区间成交量的一部分
        if side in ("buy", "both") and allow_buy:
            legs.append(("buy", q.bid, seg_sell * QUEUE_SHARE))
        if side in ("sell", "both") and allow_sell:
            legs.append(("sell", q.ask, seg_buy * QUEUE_SHARE))
        if not legs:
            res.skipped["leg_blocked"] = res.skipped.get("leg_blocked", 0) + 1
            continue

        for leg_side, px, avail_qty in legs:
            # [F91 2026-09-14] 减仓腿**精确平仓**（与实盘 `plan_tick` 同口径）：
            # 固定 dollar 腿量在两腿 mid 不同时必然留下残差 → 单向库存漂移、
            # 账本/运行态分叉。减仓方向取 min(|现仓|, 队列份额)。
            _pos = float(book.qty(symbol) or 0.0)
            _reducing = ((leg_side == "sell" and _pos > 0)
                         or (leg_side == "buy" and _pos < 0))
            _target_qty = abs(_pos) if _reducing else qty
            fill_qty = min(_target_qty, float(avail_qty))
            if fill_qty * px < MIN_FILL_NOTIONAL:
                res.skipped["no_queue"] = res.skipped.get("no_queue", 0) + 1
                continue
            d = book.apply_fill(symbol=symbol, side=leg_side, qty=fill_qty, fill_px=px,
                                mid_px=mid, fee_rate=params_maker_fee(venue),
                                now_ts=now_ts)
            res.fills += 1
            res.notional += d["notional"]
            res.spread_usd += d["spread_usd"]
            res.price_usd += d["price_usd"]
            res.fee_usd += d["fee_usd"]
            one_side = abs(book.notional(symbol, mid))
            if one_side > res.max_one_side_usd:
                res.max_one_side_usd = one_side
            if return_fills:
                res.fill_log.append({
                    "ts_ms": int(ots[i]), "symbol": symbol, "side": leg_side,
                    "notional": round(float(d["notional"]), 4),
                    "net_usd": round(float(d["net_usd"]), 6),
                    "net_bp": round(float(d["net_usd"]) / max(1e-9, d["notional"]) * 1e4, 4),
                    "flatten": False,
                    "net_position_usd": round(float(book.notional(symbol, mid)), 4),
                    "mid": mid,
                })
            f = int(np.searchsorted(fold_edges, i, "right")) - 1
            if 0 <= f < n_folds:
                fold_buckets[f].append(d["net_usd"] / max(1e-9, d["notional"]) * 1e4)
            if record:
                _record_fill(symbol=symbol, side=leg_side, qty=fill_qty, fill_px=px,
                             mid_px=mid, fee_rate=params_maker_fee(venue),
                             notional=d["notional"], ts_ms=ots[i])

    res.avg_hold_snapshots = (hold_snaps / cycles) if cycles > 0 else 0.0
    res.net_usd = res.spread_usd + res.price_usd + res.fee_usd
    for f, arr in enumerate(fold_buckets):
        if len(arr) < 20:
            continue
        a = np.array(arr)
        t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if a.std() > 0 else 0.0
        res.folds.append({"fold": f, "n": int(len(a)),
                          "net_bp": round(float(a.mean()), 4), "t": round(float(t), 3)})
    return res


def params_maker_fee(venue: str) -> float:
    """从真实费率表取 maker 费率（Aster 0%）。"""
    try:
        from backend.services.exchange.paper_exchange_simulator import DEFAULT_EXCHANGE_RULES
        r = DEFAULT_EXCHANGE_RULES.get((venue or "").lower())
        return float(r.maker_fee_rate) if r else 0.0
    except Exception:
        return 0.0


def _record_fill(*, symbol: str, side: str, qty: float, fill_px: float, mid_px: float,
                 fee_rate: float, notional: float, ts_ms: int) -> None:
    """把影子期成交写入六维账本（失败不抛）。"""
    try:
        from datetime import datetime, timezone

        from backend.services import lane_ledger

        lane_ledger.record_fill(
            lane_id="mm_asterdex", symbol=symbol, side=side, qty=qty,
            fill_px=fill_px, mid_px=mid_px, fee_rate=fee_rate,
            ts=datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc),
            meta={"source": "F59_replay", "notional": round(notional, 4)},
        )
    except Exception as e:
        logger.debug("[F59] record_fill 失败: %s", e)


def replay_all(
    symbols: Optional[List[str]] = None,
    *,
    venue: str = DEFAULT_VENUE,
    equity: float = DEFAULT_EQUITY,
    start_ts: Optional[int] = None,
    record: bool = False,
) -> Dict[str, Any]:
    """回放全部标的，汇总成达标报告。"""
    syms = symbols or DEFAULT_SYMBOLS
    series_cache = {s: _load_series(s, venue, start_ts) for s in syms}
    # 回放窗口 = 各标的盘口时间跨度的最大值（影子期算成交率基线的分母）
    window_days = 0.0
    for ser in series_cache.values():
        ots = ser[0]
        if len(ots) >= 2:
            window_days = max(window_days,
                              (int(ots[-1]) - int(ots[0])) / 86400_000.0)
    results = [replay_symbol(s, venue=venue, equity=equity, start_ts=start_ts,
                             record=record, series=series_cache[s]) for s in syms]
    total_notional = sum(r.notional for r in results)
    total_net = sum(r.net_usd for r in results)
    total_spread = sum(r.spread_usd for r in results)
    total_price = sum(r.price_usd for r in results)
    total_fee = sum(r.fee_usd for r in results)
    total_fills = sum(r.fills for r in results)

    def _bp(x: float) -> float:
        return round(x / total_notional * 1e4, 3) if total_notional > 0 else 0.0

    # 组合级分折（按各币同折合并）
    combined: Dict[int, List[float]] = {}
    for r in results:
        for f in r.folds:
            combined.setdefault(int(f["fold"]), []).append(float(f["net_bp"]))
    folds = []
    for k, v in sorted(combined.items()):
        a = np.array(v)
        t = (float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a))))
             if len(a) > 1 and a.std() > 0 else 0.0)
        folds.append({"fold": k, "n": len(v), "net_bp": round(float(a.mean()), 4),
                      "t": round(t, 3),
                      "positive_symbols": int(sum(1 for x in v if x > 0))})

    return {
        "venue": venue, "equity": equity, "symbols": syms,
        "fills": total_fills, "notional": round(total_notional, 2),
        # 回放窗口与「每标的每小时成交数」——影子期算 fill_rate_ratio 的基线
        "window_days": round(window_days, 3),
        "symbols_n": len(syms),
        "fills_per_symbol_hour": (round(total_fills / (window_days * 24.0) / max(1, len(syms)), 4)
                                  if window_days > 0 else None),
        "spread_usd": round(total_spread, 4),
        "price_usd": round(total_price, 4),
        "fee_usd": round(total_fee, 4),
        "net_usd": round(total_net, 4),
        "spread_bp": _bp(total_spread),
        "price_bp": _bp(total_price),
        "fee_bp": _bp(total_fee),
        "net_bp": _bp(total_net),
        "flattens": sum(r.flattens for r in results),
        "flatten_usd": round(sum(r.flatten_usd for r in results), 4),
        "folds": folds,
        "per_symbol": [r.to_dict() for r in results],
    }


def push_to_registry(report: Dict[str, Any]) -> bool:
    """把影子期结果写入 lane_registry（edge + health）。

    晋级判定用的 folds 直接取回放的真实分折（含真实 t），**不做任何填充**；
    `max_dd_pct` / `fill_rate_ratio` 回放无法验证 → 保持 None（fail-closed）。
    """
    try:
        from backend.services import lane_registry as reg

        folds = [{"net_bp": f["net_bp"], "t": f.get("t"), "n": f["n"]}
                 for f in report.get("folds", [])]
        edge = {
            "gross_bp": report["spread_bp"], "cost_bp": abs(report["fee_bp"]),
            "net_bp": report["net_bp"], "t": report.get("t"),
            "n": report["fills"], "folds": folds,
            "spread_bp": report["spread_bp"], "price_bp": report["price_bp"],
            "fee_bp": report["fee_bp"],
            "max_dd_pct": None,               # 回放未跟踪权益曲线 → 未验证
            "fill_rate_ratio": None,          # 真实成交率需实盘对照 → 未验证
            "source": "f59_replay",           # 晋升判定的来源白名单
            "as_of": datetime.now(timezone.utc).isoformat(),
            "note": (f"F59 影子期回放（真实盘口+真实费率，maker 0%）；"
                     f"{report['fills']} fills / ${report['notional']:.0f} 名义；"
                     f"打对手价平仓 {report.get('flattens', 0)} 次"),
        }
        reg.update_edge("mm_asterdex", edge)
        reg.update_health("mm_asterdex", {
            "data_age_sec": None, "breaker": None,
            "note": f"{report['fills']} fills / {report['notional']:.0f} notional",
        })
        return True
    except Exception as e:
        logger.warning("[F59] push_to_registry 失败: %s", e)
        return False
