# -*- coding: utf-8 -*-
"""trend_core — E1 核心趋势引擎的**唯一规则源**（回测 / 实盘 / 漂移对账三端同核）。

v2《亏损根因与正收益改造》第一节实测过门的唯一收益源：

    主流 8 币多头趋势跟踪（BTC/ETH/SOL/BNB/XRP/DOGE/LINK/AVAX），日线级
    入场 = 收盘 > EMA200 且 EMA20 > EMA50 > EMA100          （ema_stack）
    出场 = 入场规则失效 或 收盘 < Chandelier(最高收盘 − 3 × ATR20)
    等权 12.5%/币、无杠杆、信号次日执行、每边 5bp 手续费 + 2bp 滑点、多头付 7.5% 年化资金费
    → 2020-01 至 2026-09：CAGR 56%、Sharpe 1.15、MDD −49%（同期 BTC 43% / 0.90 / −77%）

本模块只做三件事，全部是**纯函数、无 DB、无 LLM、无前视**（只用截至当前 bar 的数据）：

  1. indicators()          EMA20/50/100/200、ATR20（TR 的简单均值，与回测一致）、60 日实现波动
  2. entry_signal()        ema_stack（回测验证过的规则）或 l1_score（trend_layer 五票制，仅供对照）
  3. run_state_machine()   逐 bar 的持仓状态机：state[t] = 处理完 bar t 后是否持仓；给出 Chandelier 止损序列
  4. target_weights()      等权 或 vol-target（名义 = 目标波动 / 实现波动，单币帽、单笔风险帽、总敞口帽）

对齐约定（回测与实盘共用）：
  - `state[t]` 是「bar t 收盘后」的决策，应持有到 bar t+1 收盘；实盘 00:05 UTC 日任务读到的最后一根
    已收盘 bar 就是 t，所以「今天应有仓位」= state[t]。
  - 回测里 `exec_lag_days` 再多延一天（信号次日成交）是保守口径；实盘执行更早，不构成漂移。

依赖：numpy / pandas。被 backend/research/trend_sleeve_backtest.py（回测 + 漂移）与
backend/services/long_trend_v2.py（实盘入场闸 / 持仓管理）共同引用。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# 拍板的核心币池（v2 E1；v3 沿用）。
DEFAULT_CORE_SYMBOLS: Tuple[str, ...] = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "AVAX")


def core_symbols() -> List[str]:
    """`TREND_CORE_SYMBOLS` 环境变量（逗号分隔）→ 大写 base 列表；缺省为拍板的 8 主流。"""
    raw = (os.getenv("TREND_CORE_SYMBOLS", "") or "").strip()
    if not raw:
        return list(DEFAULT_CORE_SYMBOLS)
    out: List[str] = []
    for tok in raw.split(","):
        s = tok.strip().upper().split("/")[0].split("-")[0]
        if s.endswith("USDT") and len(s) > 4:
            s = s[:-4]
        if s and s not in out:
            out.append(s)
    return out or list(DEFAULT_CORE_SYMBOLS)


@dataclass
class TrendRules:
    """E1 规则参数（默认值 = 回测验证口径；实盘与回测共用同一份）。"""

    entry_rule: str = "ema_stack"          # ema_stack | l1_score
    l1_min_score: int = 3                  # 仅 entry_rule=l1_score 时用
    chandelier_mult: float = 3.0           # Chandelier 倍数（回测 3.0）
    atr_period: int = 20                   # ATR 窗口（回测 20，TR 简单均值）
    stop_ratchet: bool = False             # True=止损只上移（ATR 放大不下调）；False=回测口径（每日 hi − mult×ATR 重算）
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 100
    ema_regime: int = 200
    # 权重
    weighting: str = "equal"               # equal | vol_target | portfolio_vol_target
    vol_target: float = 0.35               # 目标年化波动（v3 拍板 35%；vol_target=每币，portfolio_vol_target=趋势桶整体）
    vol_lookback: int = 60                 # 实现波动窗口（日）
    max_weight_per_symbol: float = 0.35    # 单币名义 ≤ 35% 权益
    gross_cap: float = 1.0                 # 总名义 ≤ 1.0 × 权益（无杠杆；RiskEngine 另有 ≤3x 硬顶）
    risk_per_trade_pct: Optional[float] = None   # 单笔风险帽（名义 × 初始止损距离 ≤ 该比例 × 权益）；None=不启用
    max_positions: int = 8                 # 同时持仓上限（等权时每仓 = gross_cap / max_positions）

    @classmethod
    def from_env(cls) -> "TrendRules":
        """实盘引擎用：从环境变量取参数（缺省即回测口径 + v3 拍板的 vol-target）。"""

        def _f(k: str, d: float) -> float:
            try:
                return float(os.getenv(k, d))
            except Exception:
                return d

        def _i(k: str, d: int) -> int:
            try:
                return int(float(os.getenv(k, d)))
            except Exception:
                return d

        # 单笔风险帽默认 1.25%（对照表 A3：CAGR 28% / Sharpe 1.15 / MDD −31.5%，趋势桶 60% 权重下组合回撤 ≈ −19%，
        # 落在 20%/30% 两级 TradingState 触发线之内）；方案原文 0.75% 对应 A2（CAGR 16.8% / MDD −20%），
        # 设 TREND_RISK_PER_TRADE_PCT=0.0075 即切换；设 0 关闭该帽。
        rpt = _f("TREND_RISK_PER_TRADE_PCT", 0.0125)
        return cls(
            entry_rule=(os.getenv("TREND_ENTRY_RULE", "ema_stack") or "ema_stack").strip().lower(),
            l1_min_score=_i("LONG_V2_L1_UP_SCORE", 3),
            chandelier_mult=_f("TREND_CHANDELIER_MULT", 3.0),
            atr_period=_i("TREND_ATR_PERIOD", 20),
            stop_ratchet=str(os.getenv("TREND_STOP_RATCHET", "false")).strip().lower() in ("1", "true", "yes", "on"),
            weighting=(os.getenv("TREND_WEIGHTING", "vol_target") or "vol_target").strip().lower(),
            vol_target=_f("TREND_VOL_TARGET", 0.35),
            vol_lookback=_i("TREND_VOL_LOOKBACK", 60),
            max_weight_per_symbol=_f("TREND_MAX_WEIGHT_PER_SYMBOL", 0.35),
            gross_cap=_f("TREND_GROSS_CAP", 1.0),
            risk_per_trade_pct=(rpt if rpt > 0 else None),
            max_positions=_i("TREND_MAX_POSITIONS", 8),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────── 指标（因果） ───────────────────────────

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    return pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def indicators(df: pd.DataFrame, rules: Optional[TrendRules] = None) -> pd.DataFrame:
    """单币 1d OHLC（升序，列 open/high/low/close）→ 指标表（与 df 同索引）。

    ATR 用 TR 的 rolling(atr_period) 简单均值（回测口径）；realized_vol = 日收益 rolling(60) std × √365。
    """
    r = rules or TrendRules()
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    out = pd.DataFrame(index=df.index)
    out["close"] = close
    out["e_fast"] = _ema(close, r.ema_fast)
    out["e_mid"] = _ema(close, r.ema_mid)
    out["e_slow"] = _ema(close, r.ema_slow)
    out["e_regime"] = _ema(close, r.ema_regime)
    out["atr"] = true_range(high, low, close).rolling(r.atr_period).mean()
    ret = close.pct_change(fill_method=None)
    out["realized_vol"] = ret.rolling(r.vol_lookback).std() * np.sqrt(365.0)
    return out


def entry_signal(df: pd.DataFrame, rules: Optional[TrendRules] = None, ind: Optional[pd.DataFrame] = None) -> pd.Series:
    """逐 bar 入场信号（bool）。ema_stack：close>EMA200 且 EMA20>EMA50>EMA100。"""
    r = rules or TrendRules()
    if r.entry_rule == "l1_score":
        from backend.services.trend_layer import classify_series

        cs = classify_series(df)
        return (cs["score"] >= r.l1_min_score).fillna(False)
    ind = ind if ind is not None else indicators(df, r)
    sig = (ind["close"] > ind["e_regime"]) & (ind["e_fast"] > ind["e_mid"]) & (ind["e_mid"] > ind["e_slow"])
    return sig.fillna(False)


# ─────────────────────────── 状态机 ───────────────────────────

@dataclass
class SymbolState:
    """单币逐 bar 状态机输出。"""

    state: pd.Series           # 处理完 bar t 后是否持仓（0/1）
    stop: pd.Series            # 持仓期间的 Chandelier 止损（未持仓 NaN）
    highest_close: pd.Series   # 持仓期间最高收盘
    entry_close: pd.Series     # 本段持仓的入场收盘价
    entry_stop: pd.Series      # 本段持仓的初始止损（入场收盘 − mult × ATR）
    n_entries: int
    n_exits: int
    exit_reasons: Dict[str, int] = field(default_factory=dict)


def run_state_machine(df: pd.DataFrame, rules: Optional[TrendRules] = None,
                      ind: Optional[pd.DataFrame] = None, sig: Optional[pd.Series] = None) -> SymbolState:
    """回测与实盘同一状态机：

        持仓中：hi = max(hi, close_t)；stop = hi − mult × ATR_t；close_t < stop 或 sig_t=False → 出场
        空仓中：sig_t=True → 入场（hi = close_t）；当日不再做止损判定
    与 v2 回测脚本逐 bar 等价（先判出场、再判入场；同一 bar 不会先出再进）。
    """
    r = rules or TrendRules()
    ind = ind if ind is not None else indicators(df, r)
    sig = sig if sig is not None else entry_signal(df, r, ind)
    c = ind["close"].to_numpy(dtype=float)
    a = ind["atr"].to_numpy(dtype=float)
    s = sig.to_numpy(dtype=bool)
    n = len(c)
    state = np.zeros(n, dtype=float)
    stop = np.full(n, np.nan)
    hi_arr = np.full(n, np.nan)
    ent_c = np.full(n, np.nan)
    ent_stop = np.full(n, np.nan)
    in_pos = False
    hi = 0.0
    cur_entry = np.nan
    cur_entry_stop = np.nan
    n_in = n_out = 0
    reasons: Dict[str, int] = {"chandelier": 0, "rule_invalid": 0}
    for t in range(n):
        ct = c[t]
        if np.isnan(ct):
            state[t] = 1.0 if in_pos else 0.0
            continue
        at = a[t] if not np.isnan(a[t]) else 0.0
        if in_pos:
            hi = max(hi, ct)
            st = hi - r.chandelier_mult * at
            if r.stop_ratchet and t > 0 and not np.isnan(stop[t - 1]):
                st = max(st, stop[t - 1])
            if ct < st:
                in_pos = False
                n_out += 1
                reasons["chandelier"] += 1
            elif not s[t]:
                in_pos = False
                n_out += 1
                reasons["rule_invalid"] += 1
            else:
                stop[t] = st
                hi_arr[t] = hi
                ent_c[t] = cur_entry
                ent_stop[t] = cur_entry_stop
        else:
            if s[t]:
                in_pos = True
                hi = ct
                cur_entry = ct
                cur_entry_stop = ct - r.chandelier_mult * at
                stop[t] = cur_entry_stop
                hi_arr[t] = hi
                ent_c[t] = cur_entry
                ent_stop[t] = cur_entry_stop
                n_in += 1
        state[t] = 1.0 if in_pos else 0.0
    idx = df.index
    return SymbolState(
        state=pd.Series(state, index=idx), stop=pd.Series(stop, index=idx),
        highest_close=pd.Series(hi_arr, index=idx), entry_close=pd.Series(ent_c, index=idx),
        entry_stop=pd.Series(ent_stop, index=idx), n_entries=n_in, n_exits=n_out, exit_reasons=reasons,
    )


# ─────────────────────────── 权重 ───────────────────────────

def portfolio_vol_weights(sel_row: np.ndarray, vols: np.ndarray, cov: Optional[np.ndarray], target: float,
                          fallback_rho: float = 0.7) -> np.ndarray:
    """单日：被选中的持仓按 1/σ 分配，再整体缩放使组合年化波动 = target。

    sel_row  bool[n]      当日持仓掩码
    vols     float[n]     各币年化实现波动
    cov      float[n,n]   各币日收益协方差（年化）；None 或含 NaN 的位置用 fallback_rho 补
    返回 float[n] 权重（未做单币帽 / 总敞口帽，由调用方处理）
    """
    n = len(sel_row)
    w = np.zeros(n)
    idx = np.where(sel_row & np.isfinite(vols) & (vols > 0))[0]
    if len(idx) == 0:
        return w
    raw = 1.0 / vols[idx]
    if cov is None:
        C = np.outer(vols[idx], vols[idx]) * fallback_rho
        np.fill_diagonal(C, vols[idx] ** 2)
    else:
        C = np.array(cov[np.ix_(idx, idx)], dtype=float)
        fb = np.outer(vols[idx], vols[idx]) * fallback_rho
        np.fill_diagonal(fb, vols[idx] ** 2)
        C = np.where(np.isfinite(C), C, fb)
    var = float(raw @ C @ raw)
    if var <= 0:
        return w
    k = target / np.sqrt(var)
    w[idx] = raw * k
    return w


def target_weights(state: pd.DataFrame, realized_vol: pd.DataFrame, rules: Optional[TrendRules] = None,
                   stop_distance_pct: Optional[pd.DataFrame] = None,
                   daily_returns: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """持仓 0/1 矩阵 → 目标名义权重矩阵（占权益比例）。

    equal                : 每仓 gross_cap / max_positions（回测 8 币 = 12.5%）
    vol_target           : w_i = vol_target / realized_vol_i（每币独立目标波动；方案原文口径）
    portfolio_vol_target : 1/σ 分配后整体缩放，使**趋势桶**年化波动 = vol_target（用 60 日协方差；
                           拍板文字"趋势桶目标年化波动 35%"的字面口径）
    三者之后统一：单币帽 max_weight_per_symbol → 单笔风险帽 risk_per_trade / 初始止损距离 →
    Σw > gross_cap 时整体等比缩到 gross_cap（不加杠杆）。
    超过 max_positions 的持仓按列序丢弃（回测同款 rank 'first'）。
    """
    r = rules or TrendRules()
    pos = state.fillna(0.0).astype(float)
    sel = pos.where(pos > 0).rank(axis=1, method="first") <= r.max_positions
    sel = sel.fillna(False) & (pos > 0)
    if r.weighting == "equal":
        return sel.astype(float) * (r.gross_cap / max(1, r.max_positions))
    rv = realized_vol.reindex_like(pos).astype(float)
    if r.weighting == "portfolio_vol_target":
        cols = list(pos.columns)
        cov_roll = None
        if daily_returns is not None:
            dr = daily_returns.reindex(index=pos.index, columns=cols).astype(float)
            cov_roll = dr.rolling(r.vol_lookback, min_periods=max(20, r.vol_lookback // 2)).cov() * 365.0
        out = np.zeros(pos.shape)
        sel_np = sel.to_numpy(dtype=bool)
        rv_np = rv.to_numpy(dtype=float)
        for i, ts in enumerate(pos.index):
            if not sel_np[i].any():
                continue
            cov_i = None
            if cov_roll is not None:
                try:
                    cov_i = cov_roll.loc[ts].reindex(index=cols, columns=cols).to_numpy(dtype=float)
                except KeyError:
                    cov_i = None
            out[i] = portfolio_vol_weights(sel_np[i], rv_np[i], cov_i, r.vol_target)
        w = pd.DataFrame(out, index=pos.index, columns=cols).clip(upper=r.max_weight_per_symbol)
    else:
        w = (r.vol_target / rv).clip(upper=r.max_weight_per_symbol)
    if r.risk_per_trade_pct and stop_distance_pct is not None:
        sd = stop_distance_pct.reindex_like(pos).astype(float)
        risk_cap = (r.risk_per_trade_pct / sd.where(sd > 0)).clip(upper=r.max_weight_per_symbol)
        w = pd.DataFrame(np.minimum(w.to_numpy(), risk_cap.fillna(w).to_numpy()), index=w.index, columns=w.columns)
    w = w.where(sel, 0.0).fillna(0.0)
    g = w.sum(axis=1)
    scale = (r.gross_cap / g.where(g > 0)).clip(upper=1.0).fillna(0.0)
    return w.mul(scale, axis=0)


def size_one(equity: float, realized_vol: Optional[float], stop_distance_pct: Optional[float],
             rules: Optional[TrendRules] = None, n_open_after: int = 1, gross_open_notional: float = 0.0) -> Dict[str, Any]:
    """实盘单笔定仓（PositionConstruction 趋势车道调用）：返回名义/权重与被哪个帽子夹住。

    equity              账户权益
    realized_vol        60 日实现波动（年化）；None → 退化为等权
    stop_distance_pct   初始止损距离（Chandelier：mult × ATR / close）
    n_open_after        本笔成交后的持仓数（等权用）
    gross_open_notional 当前已有名义（总敞口帽用）
    """
    r = rules or TrendRules()
    eq = max(0.0, float(equity or 0.0))
    caps: List[str] = []
    if r.weighting == "equal" or not realized_vol or realized_vol <= 0:
        w = r.gross_cap / max(1, r.max_positions)
        caps.append("equal_weight")
    else:
        w = r.vol_target / float(realized_vol)
        caps.append(f"vol_target({r.vol_target:.2f}/{realized_vol:.2f})")
        if w > r.max_weight_per_symbol:
            w = r.max_weight_per_symbol
            caps.append(f"max_weight_per_symbol({r.max_weight_per_symbol:.2f})")
        if r.risk_per_trade_pct and stop_distance_pct and stop_distance_pct > 0:
            rc = r.risk_per_trade_pct / float(stop_distance_pct)
            if rc < w:
                w = rc
                caps.append(f"risk_per_trade({r.risk_per_trade_pct:.4f}/{stop_distance_pct:.4f})")
    notional = eq * w
    room = max(0.0, eq * r.gross_cap - max(0.0, float(gross_open_notional or 0.0)))
    if notional > room:
        notional = room
        caps.append(f"gross_cap({r.gross_cap:.2f})")
    return {"weight": (notional / eq) if eq > 0 else 0.0, "notional": round(notional, 4), "caps": caps,
            "risk_pct": round((notional / eq) * float(stop_distance_pct or 0.0), 5) if eq > 0 else 0.0}
