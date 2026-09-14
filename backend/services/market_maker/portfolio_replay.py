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
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from backend.services.market_maker.core import (
    InventoryBook,
    LaneRiskLimits,
    QuoteParams,
    realized_vol_bp,
)
# [F94c] 在挂腿风险预留口径必须与实盘 tick 完全一致（同一函数，避免两处漂移）
from backend.services.market_maker.runner import pending_contrib as _pending_contrib  # noqa: E402

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


def compute_vol_baselines(mid_series_by_symbol: Dict[str, List[float]],
                          *, window: int = 20) -> Dict[str, float]:
    """各币「已实现波动基准」= 全窗逐期中位（回放与实盘共用同一口径）。

    [F96 2026-09-14] 抽成单一实现：此前 `portfolio_replay` 内联算、注册表另存一份、
    实盘只对注册表里**存在**的币覆写（其余沿用持久化的陈旧值）——实测 ETH/BNB/XRP/SOL
    的实盘基准比回放低 15~21%。`k_vol>0` 时 σ=vol/基准−1 直接决定挂宽，这个漂移会
    让实盘与回放**挂不同宽度的单**。现在：回放、锚定脚本、实盘校验全部走这一个函数。
    """
    out: Dict[str, float] = {}
    for s, ms in mid_series_by_symbol.items():
        vals: List[float] = []
        if len(ms) >= window + 2:
            vals = [realized_vol_bp(ms[j - window:j + 1], window)
                    for j in range(window, len(ms))]
            vals = [v for v in vals if v > 0]
        out[s] = float(np.median(vals)) if vals else 0.0
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
    # [F84 2026-09-14] 复利模拟：>0 时每腿名义 = 该比例 × 运行权益（权益随已实现
    # 盈亏滚动），equity 参数成为初始权益；敞口上限也随运行权益缩放（与实盘
    # 「按权益比例的风控」语义一致）。None/0 = 固定腿量（旧行为）。
    fill_notional_ratio: Optional[float] = None,
    # [F95 2026-09-14] 影子对齐校验：以**实盘运行态**为初值从指定时刻起跑，
    # 使回放与实盘在同一状态、同一数据上逐桶推进——「实盘有没有漏成交」才可判定。
    init_states: Optional[Dict[str, Any]] = None,
    start_ts_ms: Optional[int] = None,
    # [F98 2026-09-14] 车道级闸门（日亏全停 / 毒性流暂停）此前**回放里测不到**：
    # `plan_tick` 的 day_pnl 由 `MM_LANE_LIMITS_ENFORCE` 门控，回放永远传 0
    # ⇒ 配置里的 daily_loss_stop_pct=10 从未被任何实验覆盖。这里显式支持：
    # 逐 UTC 日累计已实现盈亏并传入，闸门是否生效仍由 limits 决定。
    enforce_lane_limits: bool = False,
) -> Dict[str, Any]:
    """按合并时间线回放多个标的，**共享库存账本**。"""
    from backend.services.market_maker.runner import SymbolState, plan_tick

    params = params or QuoteParams(w_base_bp=8.0, k_inv=0.6)
    limits = limits or LaneRiskLimits()
    data = data or _load_all(symbols, venue)

    states = {s: SymbolState(symbol=s) for s in symbols}
    # [F95] 影子对齐：以实盘运行态为初值（深拷贝，不改调用方对象）
    if init_states:
        for _s, _st in init_states.items():
            if _s not in states or _st is None:
                continue
            try:
                states[_s] = SymbolState.from_dict(_st.to_dict())
            except Exception:
                states[_s] = _st if isinstance(_st, SymbolState) else states[_s]
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
    vol_baseline: Dict[str, float] = compute_vol_baselines(_mid_series)
    for s in symbols:
        states[s].vol_baseline_bp = float(vol_baseline.get(s) or 0.0)
    # 每个币的下一个快照下标
    idx = {s: 0 for s in symbols}
    # 合并时间线：所有币的快照时间戳并集（升序）
    timeline = np.unique(np.concatenate([data[s]["ots"] for s in symbols]))
    if start_ts_ms:
        timeline = timeline[timeline >= int(start_ts_ms)]
    # [F95] 从实盘状态起跑时：各币下标要跳到 start 之后（否则重复消费历史快照）
    if start_ts_ms:
        for s in symbols:
            idx[s] = int(np.searchsorted(data[s]["ots"], int(start_ts_ms), "left"))
    fills_log: List[Dict[str, Any]] = []
    skips: Dict[str, int] = {}
    notional_sum = 0.0
    per_symbol: Dict[str, Dict[str, float]] = {
        s: {"fills": 0, "net_usd": 0.0, "notional": 0.0} for s in symbols}
    # [F84] 复利：运行权益与腿量（初始 equity 只作起点）
    _ratio = float(fill_notional_ratio or 0.0)
    running_equity = float(equity)
    capped_fill_count = 0   # 队列份额截断腿量的次数（容量上限观测）
    # [F95] 与实盘同口径的行为计数：报价侧分布 + 闸门拦截分布。
    # 实盘/回放速率不一致时，用来判定差异在「报价行为」还是「成交判定」。
    side_counts: Dict[str, int] = {"both": 0, "one": 0, "none": 0}
    skip_counts: Dict[str, int] = {}
    # [F98] 车道级闸门：逐 UTC 日累计已实现盈亏（供 daily_loss_stop_pct）
    _day_key: Optional[str] = None
    _day_pnl = 0.0
    lane_pause_counts: Dict[str, int] = {}
    # [F98] 闸门「离触发有多远」的观测：最大单日亏损、最大毒性连击
    max_daily_loss_usd = 0.0
    max_toxic_streak = 0
    # [F94c] 风险实况：由**真实账本**逐快照统计（外部用成交日志重建会失真——
    # 实测重建值偏离真实值 2 倍以上，导致上限是否生效无法判断）
    max_net_usd = 0.0
    max_gross_usd = 0.0
    breach_snapshots = 0
    # [F94] 在挂同向腿的风险预留（跨币累加，逐快照重建）
    pending: Dict[str, float] = {"up": 0.0, "down": 0.0, "gross": 0.0}

    for ts in timeline:
        # [F98] 车道级闸门输入：切换 UTC 日则重置日亏累计
        if enforce_lane_limits:
            _dk = time.strftime("%Y-%m-%d", time.gmtime(int(ts) / 1000.0))
            if _dk != _day_key:
                # 收尾上一天：记录最大单日亏损（闸门「离触发多远」的证据）
                max_daily_loss_usd = min(max_daily_loss_usd, _day_pnl)
                _day_key, _day_pnl = _dk, 0.0
        # [F94c] 每个快照开始时重建预留：按「加仓/减仓」区分（减仓腿只可能减掉现仓）
        pending["up"] = 0.0
        pending["down"] = 0.0
        pending["gross"] = 0.0
        _leg_now = (max(10.0, _ratio * running_equity) if _ratio > 0 else fill_notional)
        _marks_now = {}
        for _s in symbols:
            _i = idx[_s]
            _d0 = data[_s]
            if _i < len(_d0["bb"]):
                _marks_now[_s] = float((_d0["bb"][_i] + _d0["ba"][_i]) / 2)
        for _s in symbols:
            _u, _d, _g = _pending_contrib(states[_s], _marks_now.get(_s, 0.0), _leg_now)
            pending["up"] += _u
            pending["down"] += _d
            pending["gross"] += _g
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
            # [F86] 决策时可见的「上一已完成桶」主动流失衡 OFI∈[-1,1]：
            # 取 ts <= 当前快照 的最后一个成交桶（信息集约束：不含未来）
            _k = int(np.searchsorted(d["tts"], d["ots"][i], "right")) - 1
            ofi = 0.0
            if _k >= 0:
                _b, _s = float(d["bv"][_k]), float(d["sv"][_k])
                if _b + _s > 0:
                    ofi = (_b - _s) / (_b + _s)
            # [F94 2026-09-14] 在挂同向腿的风险预留：与实盘 tick 同口径——
            # 每个币重挂前先撤掉自己的旧贡献，挂完再按新单加回。此前闸门不看
            # 在挂单 ⇒ 多币同向同时成交把净敞口顶到上限的 7.9 倍（回放实测）。
            _leg_nt = (max(10.0, _ratio * running_equity) if _ratio > 0
                       else fill_notional)
            _st = states[s]
            _u0, _d0, _g0 = _pending_contrib(_st, marks.get(s, 0.0), _leg_nt)
            pending["up"] = max(0.0, pending["up"] - _u0)
            pending["down"] = max(0.0, pending["down"] - _d0)
            pending["gross"] = max(0.0, pending["gross"] - _g0)
            dec, _meta = plan_tick(
                state=states[s], mid=mid, seg_low=seg_low, seg_high=seg_high,
                seg_taker_sell=seg_sell, seg_taker_buy=seg_buy,
                now_ts=float(d["ots"][i]) / 1000.0, params=params, limits=limits,
                # [F84] 复利模式：腿量与权益均用运行值；固定模式权益恒定
                # （权益随盈亏漂移会把敞口上限压到腿量以下 ⇒ 入场侧永久死锁，
                #  实测 25928 次 symbol_exposure 拦截、6 天仅 5 笔）。
                equity=(running_equity if _ratio > 0 else equity),
                fill_notional=(max(10.0, _ratio * running_equity)
                               if _ratio > 0 else fill_notional),
                taker_fee_bp=4.0, maker_fee_bp=0.0, half_spread=half_spread,
                sigma_norm=sigma, book=book, marks=marks,
                ofi=ofi, pending=pending,
                # [F98] 日亏闸输入（仅在显式要求时非零；否则与旧行为逐字一致）
                day_pnl_usd=(_day_pnl if enforce_lane_limits else 0.0),
                lane_limits_enforce=(True if enforce_lane_limits else None),
            )
            if dec.lane_pause:
                _lk = dec.lane_pause.split("(")[0]
                lane_pause_counts[_lk] = lane_pause_counts.get(_lk, 0) + 1
            # [F94c] 新挂腿按加仓/减仓区分计入
            _u1, _d1, _g1 = _pending_contrib(states[s], marks.get(s, 0.0), _leg_nt)
            pending["up"] += _u1
            pending["down"] += _d1
            pending["gross"] += _g1
            if dec.skip and not dec.fills:
                key = dec.skip.split("(")[0]
                skips[key] = skips.get(key, 0) + 1
            # [F95] 行为计数（与实盘 tick 同口径）
            _kk = dec.skip.split("(")[0].strip() if dec.skip else ""
            if _kk:
                skip_counts[_kk] = skip_counts.get(_kk, 0) + 1
            if dec.bid > 0 and dec.ask > 0:
                side_counts["both"] += 1
            elif dec.bid > 0 or dec.ask > 0:
                side_counts["one"] += 1
            else:
                side_counts["none"] += 1
            max_toxic_streak = max(max_toxic_streak, int(states[s].toxic_streak or 0))
            for f in dec.fills:
                # [F84] 队列份额截断观测：qty < 期望腿量 ⇒ 容量上限在约束
                if f.qty * f.px < (fill_notional if _ratio <= 0
                                   else max(10.0, _ratio * running_equity)) * 0.9:
                    capped_fill_count += 1
                running_equity += float(f.net_usd)
                _day_pnl += float(f.net_usd)
                notional_sum += f.qty * f.px
                per_symbol[s]["fills"] += 1
                per_symbol[s]["net_usd"] += f.net_usd
                per_symbol[s]["notional"] += f.qty * f.px
                fills_log.append({
                    "ts_ms": int(d["ots"][i]), "symbol": s, "side": f.side,
                    "notional": round(f.qty * f.px, 4),
                    "net_usd": round(f.net_usd, 6), "flatten": f.is_flatten,
                    # [F96] 逐笔 markout 研究需要成交量/成交价/成交时中价
                    "qty": round(float(f.qty), 10), "px": round(float(f.px), 10),
                    "mid": round(float(f.mid), 10),
                    "net_position_usd": round(book.notional(s, mid), 4),
                })
        # [F94c] 快照末风险实况（真实账本口径）
        _n = abs(book.net_notional(marks)) if marks else 0.0
        _g = sum(abs(book.notional(k, marks.get(k, 0.0))) for k in symbols) if marks else 0.0
        max_net_usd = max(max_net_usd, _n)
        max_gross_usd = max(max_gross_usd, _g)
        if limits.max_gross_notional_ratio > 0 and \
                _g > running_equity * limits.max_gross_notional_ratio * 1.02:
            breach_snapshots += 1

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
        # [F95] 行为计数（与实盘同口径）：定位实盘/回放速率差异来源
        "side_counts": side_counts,
        "skip_counts": dict(sorted(skip_counts.items(), key=lambda kv: -kv[1])[:12]),
        # [F98] 车道级闸门触发次数（验证「配置的闸门是否真的在起作用」）
        "lane_pause_counts": lane_pause_counts,
        # [F98] 闸门裕度观测：最大单日亏损（USD，负值）与最大毒性连击
        "max_daily_loss_usd": round(min(max_daily_loss_usd, _day_pnl), 2),
        "max_toxic_streak": int(max_toxic_streak),
        "open_inventory_usd": round(gross, 2),
        "net_exposure_usd": round(book.net_notional(marks), 2),
        # [F94c] 风险实况（真实账本逐快照峰值）：用于验证上限是否真的兜住风险
        "max_net_usd": round(max_net_usd, 2),
        "max_gross_usd": round(max_gross_usd, 2),
        "cap_breach_snapshots": breach_snapshots,
        # [F84] 复利观测：最终权益与队列份额截断次数
        "final_equity": round(running_equity, 4),
        "capped_fill_count": capped_fill_count,
        "fills_log": fills_log,
    }
