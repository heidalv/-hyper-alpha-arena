# -*- coding: utf-8 -*-
"""[F73] 组合级回放：共享库存账本，让「组合净敞口上限」真正生效。

为什么需要它：
  `replay_symbol` 逐币独立模拟，每币一个 `InventoryBook`，
  因此 `LaneRiskLimits.max_net_exposure_ratio`（组合净敞口 ≤ 权益 X%）**从未生效**——
  6 个币各持 $100 多头时，组合已有 $600 同向暴露，但每个币看起来都只占 $100。
  实测事故：13:47–13:49 全市场下跌，6 币多头库存同时被平，组合亏损远超单币限额。

本模块按**合并时间线**驱动各币状态机（复用 `runner.plan_tick`，不重复实现），
所有币共享同一个 `InventoryBook`，于是组合级约束、相关性风险都能被真实模拟。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from backend.services.market_maker.core import (
    InventoryBook,
    LaneRiskLimits,
    QuoteParams,
    realized_vol_bp,
)

logger = logging.getLogger(__name__)

DEFAULT_VENUE = "asterdex"


def _load_all(symbols: List[str], venue: str = DEFAULT_VENUE) -> Dict[str, Dict[str, np.ndarray]]:
    from backend.services.market_maker import replay as rp

    out: Dict[str, Dict[str, np.ndarray]] = {}
    for s in symbols:
        ots, bb, ba, tts, lo, hi, sv, bv = rp._load_series(s, venue)
        out[s] = {"ots": ots, "bb": bb, "ba": ba, "tts": tts,
                  "lo": lo, "hi": hi, "sv": sv, "bv": bv}
    return out


def replay_portfolio(
    symbols: List[str],
    *,
    venue: str = DEFAULT_VENUE,
    equity: float = 5000.0,
    params: Optional[QuoteParams] = None,
    limits: Optional[LaneRiskLimits] = None,
    fill_notional: float = 100.0,
    max_gap_ms: int = 120_000,
    data: Optional[Dict[str, Dict[str, np.ndarray]]] = None,
) -> Dict[str, Any]:
    """按合并时间线回放多个标的，**共享库存账本**。"""
    from backend.services.market_maker.runner import SymbolState, plan_tick

    params = params or QuoteParams(w_base_bp=8.0, k_inv=0.6)
    limits = limits or LaneRiskLimits()
    data = data or _load_all(symbols, venue)

    states = {s: SymbolState(symbol=s) for s in symbols}
    book = InventoryBook()                       # ← 共享账本：组合级约束在此生效
    marks: Dict[str, float] = {}
    # [F75] 与实盘同构：每币维护 mid_hist + 已实现波动基准（实盘 tick 同款）。
    # 此前 portfolio_replay 不维护 mid_hist ⇒ plan_tick 的趋势闸/波动闸
    # （trend_blocked_side / vol_regime_blocked 都读 state.mid_hist）在组合级
    # 回放里形同虚设，且 vol_pause 误用「价差 sigma」替代「已实现波动 sigma」。
    # 两遍法：先扫各币全序列中价，算已实现波动中位数（replay_symbol 同口径）
    _mid_series: Dict[str, List[float]] = {s: [] for s in symbols}
    for s in symbols:
        d = data[s]
        for i in range(len(d["ots"])):
            _m = float((d["bb"][i] + d["ba"][i]) / 2)
            if _m > 0:
                _mid_series[s].append(_m)
    vol_baseline: Dict[str, float] = {}
    for s in symbols:
        ms = _mid_series[s]
        vb = 0.0
        if len(ms) >= 22:
            _vs = [realized_vol_bp(ms[j - 20:j + 1], 20) for j in range(20, len(ms))]
            _vs = [v for v in _vs if v > 0]
            if _vs:
                import numpy as _np
                vb = float(_np.median(_vs))
        vol_baseline[s] = vb
        states[s].vol_baseline_bp = vb
    # 每个币的下一个快照下标
    idx = {s: 0 for s in symbols}
    # 合并时间线：所有币的快照时间戳并集（升序）
    timeline = np.unique(np.concatenate([data[s]["ots"] for s in symbols]))
    fills_log: List[Dict[str, Any]] = []
    skips: Dict[str, int] = {}
    notional_sum = 0.0
    per_symbol: Dict[str, Dict[str, float]] = {
        s: {"fills": 0, "net_usd": 0.0, "notional": 0.0} for s in symbols}

    for ts in timeline:
        for s in symbols:
            d = data[s]
            i = idx[s]
            if i >= len(d["ots"]) or int(d["ots"][i]) != int(ts):
                continue
            idx[s] = i + 1
            if i + 1 >= len(d["ots"]):
                continue
            mid = float((d["bb"][i] + d["ba"][i]) / 2)
            if mid <= 0:
                continue
            marks[s] = mid
            # [F75] 与实盘同构：维护 mid_hist（趋势/波动闸的输入）
            st = states[s]
            st.mid_hist.append(mid)
            if len(st.mid_hist) > 240:
                st.mid_hist = st.mid_hist[-240:]
            # 波动归一（近 20 期**已实现波动**相对基准，与实盘 tick 同口径）
            vol_cur = realized_vol_bp(st.mid_hist, limits.vol_window)
            sigma = (max(0.0, vol_cur / vol_baseline[s] - 1.0)
                     if vol_baseline[s] > 0 else 0.0)
            half_spread = (float(d["ba"][i]) - float(d["bb"][i])) / 2.0
            # 缺口保护（同 F71）：间隔过大直接丢弃该币库存语义
            if int(d["ots"][i + 1]) - int(d["ots"][i]) > max_gap_ms:
                book.positions.pop(s, None)
                skips["data_gap"] = skips.get("data_gap", 0) + 1
                continue
            j0 = int(np.searchsorted(d["tts"], d["ots"][i], "left"))
            j1 = int(np.searchsorted(d["tts"], d["ots"][i + 1], "right"))
            seg_low = float(d["lo"][j0:j1].min()) if j1 > j0 else 0.0
            seg_high = float(d["hi"][j0:j1].max()) if j1 > j0 else 0.0
            seg_sell = float(d["sv"][j0:j1].sum()) if j1 > j0 else 0.0
            seg_buy = float(d["bv"][j0:j1].sum()) if j1 > j0 else 0.0
            dec, _meta = plan_tick(
                state=states[s], mid=mid, seg_low=seg_low, seg_high=seg_high,
                seg_taker_sell=seg_sell, seg_taker_buy=seg_buy,
                now_ts=float(d["ots"][i]) / 1000.0, params=params, limits=limits,
                equity=equity, fill_notional=fill_notional,
                taker_fee_bp=4.0, maker_fee_bp=0.0, half_spread=half_spread,
                sigma_norm=sigma, book=book, marks=marks,
            )
            if dec.skip and not dec.fills:
                key = dec.skip.split("(")[0]
                skips[key] = skips.get(key, 0) + 1
            for f in dec.fills:
                notional_sum += f.qty * f.px
                per_symbol[s]["fills"] += 1
                per_symbol[s]["net_usd"] += f.net_usd
                per_symbol[s]["notional"] += f.qty * f.px
                fills_log.append({
                    "ts_ms": int(d["ots"][i]), "symbol": s, "side": f.side,
                    "notional": round(f.qty * f.px, 4),
                    "net_usd": round(f.net_usd, 6), "flatten": f.is_flatten,
                    "net_position_usd": round(book.notional(s, mid), 4),
                })

    fills_log.sort(key=lambda x: x["ts_ms"])
    cum = np.cumsum([x["net_usd"] for x in fills_log]) if fills_log else np.array([])
    peak = np.maximum.accumulate(cum) if len(cum) else np.array([])
    max_dd = float((peak - cum).max()) if len(cum) else 0.0
    net = float(cum[-1]) if len(cum) else 0.0
    gross = sum(abs(book.notional(s, marks.get(s, 0.0))) for s in symbols)
    # 分类型诊断（与单币回放同口径对比用）
    mk = [x for x in fills_log if not x["flatten"]]
    fl = [x for x in fills_log if x["flatten"]]
    def _avg_bp(rows):
        tot = sum(x["notional"] for x in rows)
        return round(sum(x["net_usd"] for x in rows) / tot * 1e4, 4) if tot > 0 else None
    return {
        "venue": venue, "equity": equity, "symbols": symbols,
        "fills": sum(v["fills"] for v in per_symbol.values()),
        "flattens": len(fl),
        "flatten_share": round(len(fl) / len(fills_log), 4) if fills_log else None,
        "maker_net_bp": _avg_bp(mk),
        "flatten_net_bp": _avg_bp(fl),
        "notional": round(notional_sum, 2),
        "net_usd": round(net, 4),
        "net_bp": round(net / notional_sum * 1e4, 4) if notional_sum > 0 else 0.0,
        "max_dd_usd": round(max_dd, 4),
        "max_dd_pct": round(max_dd / equity * 100.0, 4) if equity > 0 else None,
        "positive_symbols": sum(1 for v in per_symbol.values() if v["net_usd"] > 0),
        "per_symbol": {s: {"fills": v["fills"], "net_usd": round(v["net_usd"], 4),
                           "net_bp": round(v["net_usd"] / v["notional"] * 1e4, 4)
                           if v["notional"] > 0 else 0.0}
                       for s, v in per_symbol.items()},
        "skipped": skips,
        "open_inventory_usd": round(gross, 2),
        "net_exposure_usd": round(book.net_notional(marks), 2),
        "fills_log": fills_log,
    }
