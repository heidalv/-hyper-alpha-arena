# -*- coding: utf-8 -*-
"""trend_sleeve_backtest — E1 趋势 sleeve 可复现回测 + 今日目标仓位 + trend_drift 漂移对账。

三个入口（同一份规则 backend/services/trend_core.py）：

  run_backtest(cfg)               从 crypto_klines(1d) 读数，输出 CAGR / vol / Sharpe / MDD / 分年 / 换手 /
                                  费用与资金费拖累 / 平均敞口 / 在场天数 / 入场与出场次数（按原因）。
                                  默认参数 = v2 实测口径，应复现 CAGR 56% / Sharpe 1.15 / MDD −49%（±5%）。
  target_positions_today(rules)   用「已收盘」日线跑同一状态机 → 每个核心币今天应持 / 应空 + 目标权重 + 止损。
  compute_trend_drift(account)    目标 vs 模拟/实盘实际 long 车道持仓 → trend_drift（缺仓 / 多仓 / 反向 / 权重偏差 /
                                  杠杆越界），写 backend/data/trend_drift/latest.json + history.jsonl。

CLI：
  python -m backend.research.trend_sleeve_backtest                 # 复现基准
  python -m backend.research.trend_sleeve_backtest --variants      # 基准 / vol-target / 风险帽 / maker0 / l1_score 对照表
  python -m backend.research.trend_sleeve_backtest --targets       # 今日目标仓位
  python -m backend.research.trend_sleeve_backtest --drift 14      # 对账户 14 做漂移对账
  python -m backend.research.trend_sleeve_backtest --json out.json # 结果落盘

数据口径：Binance 1d（库内最长，BTC 自 2017-08），`timestamp` 为秒；丢弃 ts ≥ 今天 00:00 UTC 的未收盘 bar。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from backend.services.trend_core import (
    TrendRules, core_symbols, indicators, entry_signal, run_state_machine, target_weights,
)

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trend_drift")

# v2 拍板的复现目标（±5% 相对容差；供 --variants 与验收脚本对照）
REFERENCE = {"cagr": 0.561, "sharpe": 1.15, "mdd": -0.49}


# ─────────────────────────── 数据 ───────────────────────────

def _today_utc_midnight_s() -> int:
    d = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp())


def load_daily_ohlc(symbols: Sequence[str], exchange: str = "binance", *, drop_forming: bool = True,
                    min_ts: Optional[int] = None) -> Dict[str, pd.DataFrame]:
    """crypto_klines 1d → {symbol: DataFrame[open,high,low,close] (UTC 日期索引，升序)}。"""
    from sqlalchemy import bindparam, text
    from backend.database.connection import MarketSessionLocal

    syms = [str(s).upper() for s in symbols]
    out: Dict[str, pd.DataFrame] = {}
    if not syms:
        return out
    cutoff = _today_utc_midnight_s() if drop_forming else None
    db = MarketSessionLocal()
    try:
        sql = ("SELECT symbol, timestamp, open_price, high_price, low_price, close_price FROM crypto_klines "
               "WHERE exchange = :ex AND period = '1d' AND symbol IN :syms")
        params: Dict[str, Any] = {"ex": exchange, "syms": list(syms)}
        if cutoff is not None:
            sql += " AND timestamp < :cutoff"
            params["cutoff"] = cutoff
        if min_ts is not None:
            sql += " AND timestamp >= :min_ts"
            params["min_ts"] = int(min_ts)
        sql += " ORDER BY symbol, timestamp"
        rows = db.execute(text(sql).bindparams(bindparam("syms", expanding=True)), params).fetchall()
    finally:
        db.close()
    if not rows:
        return out
    df = pd.DataFrame(rows, columns=["symbol", "timestamp", "open", "high", "low", "close"])
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["dt"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="s", utc=True).dt.normalize()
    df = df.dropna(subset=["close"]).drop_duplicates(["symbol", "dt"], keep="last")
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("dt").set_index("dt")[["open", "high", "low", "close"]]
        out[str(sym)] = g
    return out


def _wide(data: Dict[str, pd.DataFrame], col: str) -> pd.DataFrame:
    return pd.DataFrame({s: d[col] for s, d in data.items()}).sort_index()


# ─────────────────────────── 回测 ───────────────────────────

@dataclass
class BacktestConfig:
    rules: TrendRules = field(default_factory=TrendRules)
    symbols: Tuple[str, ...] = tuple(core_symbols())
    start: str = "2020-01-01"
    end: Optional[str] = None
    fee_side_bp: float = 5.0       # 每边手续费（bp）
    slip_bp: float = 2.0           # 每边滑点（bp）
    funding_ann: float = 0.075     # 多头付资金费（年化）
    exec_lag_days: int = 1         # 信号→成交延迟（天）；1 = v2 保守口径（次日成交）
    exchange: str = "binance"
    label: str = "E1 majors8 EW (v2 reference)"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["rules"] = self.rules.to_dict()
        return d


def perf_metrics(pnl: pd.Series, start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
    r = pnl.copy()
    if start:
        r = r.loc[start:]
    if end:
        r = r.loc[:end]
    r = r.dropna()
    if len(r) < 30:
        return {"n_days": int(len(r)), "cagr": None, "vol": None, "sharpe": None, "mdd": None}
    eq = (1.0 + r).cumprod()
    yrs = len(r) / 365.0
    cagr = float(eq.iloc[-1] ** (1.0 / yrs) - 1.0) if yrs > 0 else None
    sd = float(r.std())
    vol = sd * np.sqrt(365.0)
    sharpe = float(r.mean() / sd * np.sqrt(365.0)) if sd > 0 else 0.0
    dd = float((eq / eq.cummax() - 1.0).min())
    yearly = ((1.0 + r).groupby(r.index.year).prod() - 1.0)
    return {
        "n_days": int(len(r)), "years": round(yrs, 2), "total_return": float(eq.iloc[-1] - 1.0),
        "cagr": cagr, "vol": float(vol), "sharpe": sharpe, "mdd": dd,
        "yearly": {int(y): float(v) for y, v in yearly.items()},
        "first": str(r.index[0].date()), "last": str(r.index[-1].date()),
    }


@dataclass
class BacktestResult:
    config: Dict[str, Any]
    metrics: Dict[str, Any]
    since_2025: Dict[str, Any]
    benchmark_btc: Dict[str, Any]
    n_entries: int
    n_exits: int
    exit_reasons: Dict[str, int]
    ann_turnover: float
    avg_gross: float
    in_market_pct: float
    fee_drag_ann: float
    funding_drag_ann: float
    per_symbol: Dict[str, Dict[str, Any]]
    data_range: Dict[str, str]
    reference_check: Dict[str, Any]
    equity: Optional[pd.Series] = None
    pnl: Optional[pd.Series] = None

    def to_dict(self, with_curves: bool = False) -> Dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if k not in ("equity", "pnl")}
        if with_curves and self.equity is not None:
            d["equity"] = {str(k.date()): float(v) for k, v in self.equity.items()}
        return d

    def summary_line(self) -> str:
        m = self.metrics
        yr = " ".join(f"{y}:{v * 100:+.0f}%" for y, v in (m.get("yearly") or {}).items())
        return (f"{self.config.get('label', ''):44s} CAGR {m['cagr'] * 100:6.1f}% vol {m['vol'] * 100:5.1f}% "
                f"Sharpe {m['sharpe']:4.2f} MDD {m['mdd'] * 100:6.1f}% | {yr}\n"
                f"      since 2025-01: {self.since_2025.get('total_return', 0) * 100:+.1f}% MDD {self.since_2025.get('mdd', 0) * 100:.1f}% | "
                f"entries {self.n_entries} exits {self.n_exits} {self.exit_reasons} | avg gross {self.avg_gross:.2f} "
                f"in-market {self.in_market_pct * 100:.0f}% | turnover {self.ann_turnover:.1f}x/yr | "
                f"fee drag {self.fee_drag_ann * 100:.2f}%/yr funding drag {self.funding_drag_ann * 100:.2f}%/yr")


def run_backtest(cfg: Optional[BacktestConfig] = None, data: Optional[Dict[str, pd.DataFrame]] = None) -> BacktestResult:
    cfg = cfg or BacktestConfig()
    r = cfg.rules
    data = data or load_daily_ohlc(cfg.symbols, cfg.exchange)
    syms = [s for s in cfg.symbols if s in data]
    if not syms:
        raise RuntimeError(f"无 1d 数据：{cfg.symbols} @ {cfg.exchange}")
    close = _wide({s: data[s] for s in syms}, "close")
    idx = close.index
    state = pd.DataFrame(0.0, index=idx, columns=syms)
    rv = pd.DataFrame(np.nan, index=idx, columns=syms)
    sd = pd.DataFrame(np.nan, index=idx, columns=syms)
    n_in = n_out = 0
    reasons: Dict[str, int] = {}
    per_symbol: Dict[str, Dict[str, Any]] = {}
    for s in syms:
        d = data[s].reindex(idx)
        ind = indicators(d, r)
        sig = entry_signal(d, r, ind)
        sm = run_state_machine(d, r, ind, sig)
        state[s] = sm.state
        rv[s] = ind["realized_vol"]
        sd[s] = ((sm.entry_close - sm.entry_stop) / sm.entry_close).where(sm.state > 0)
        n_in += sm.n_entries
        n_out += sm.n_exits
        for k, v in sm.exit_reasons.items():
            reasons[k] = reasons.get(k, 0) + v
        held_days = int((sm.state.loc[cfg.start:] > 0).sum())
        per_symbol[s] = {"entries": sm.n_entries, "exits": sm.n_exits, "days_held_since_start": held_days,
                         "first_bar": str(d.dropna(subset=["close"]).index[0].date()) if d["close"].notna().any() else None}
    ret = close.pct_change(fill_method=None)
    w = target_weights(state, rv, r, stop_distance_pct=sd, daily_returns=ret)
    held = w.shift(1 + int(cfg.exec_lag_days)).fillna(0.0)
    pnl_gross = (held * ret).sum(axis=1, min_count=1).fillna(0.0)
    turnover = (held - held.shift(1)).abs().sum(axis=1).fillna(0.0)
    cost = turnover * ((cfg.fee_side_bp + cfg.slip_bp) / 1e4)
    gross_exp = held.sum(axis=1)
    funding = gross_exp * (cfg.funding_ann / 365.0)
    pnl = (pnl_gross - cost - funding)
    win = pnl.loc[cfg.start:cfg.end] if cfg.end else pnl.loc[cfg.start:]
    m = perf_metrics(pnl, cfg.start, cfg.end)
    since25 = perf_metrics(pnl, "2025-01-01", cfg.end) if len(pnl.loc["2025-01-01":]) > 30 else {}
    btc = perf_metrics(ret["BTC"], cfg.start, cfg.end) if "BTC" in ret.columns else {}
    yrs = max(1e-9, len(win) / 365.0)
    eq = (1.0 + win).cumprod()
    ref = {}
    if m.get("cagr") is not None:
        ref = {
            "cagr_ok": abs(m["cagr"] - REFERENCE["cagr"]) <= 0.05 * max(1.0, abs(REFERENCE["cagr"])) + 0.03,
            "sharpe_ok": abs(m["sharpe"] - REFERENCE["sharpe"]) <= 0.05 * REFERENCE["sharpe"] + 0.05,
            "mdd_ok": abs(m["mdd"] - REFERENCE["mdd"]) <= 0.05,
            "reference": REFERENCE,
        }
        ref["all_ok"] = bool(ref["cagr_ok"] and ref["sharpe_ok"] and ref["mdd_ok"])
    return BacktestResult(
        config=cfg.to_dict(), metrics=m, since_2025=since25, benchmark_btc=btc,
        n_entries=int((state.diff() > 0).loc[cfg.start:].sum().sum()), n_exits=int((state.diff() < 0).loc[cfg.start:].sum().sum()),
        exit_reasons=reasons, ann_turnover=float(turnover.loc[cfg.start:].sum() / yrs),
        avg_gross=float(gross_exp.loc[cfg.start:].mean()), in_market_pct=float((gross_exp.loc[cfg.start:] > 0).mean()),
        fee_drag_ann=float(cost.loc[cfg.start:].sum() / yrs), funding_drag_ann=float(funding.loc[cfg.start:].sum() / yrs),
        per_symbol=per_symbol,
        data_range={"first": str(idx[0].date()), "last": str(idx[-1].date()), "n_bars": int(len(idx))},
        reference_check=ref, equity=eq, pnl=win,
    )


def standard_variants(data: Optional[Dict[str, pd.DataFrame]] = None, symbols: Optional[Sequence[str]] = None) -> List[BacktestResult]:
    """对照表：复现基准 → 实盘默认（vol-target 35%）→ 风险帽 → Aster maker0 → l1_score 入场 → 实盘 lag0。"""
    syms = tuple(symbols or core_symbols())
    data = data or load_daily_ohlc(syms)
    out: List[BacktestResult] = []
    base = TrendRules()
    out.append(run_backtest(BacktestConfig(rules=base, symbols=syms, label="A0 majors8 EW12.5% fee5+2bp fund7.5% (v2 ref)"), data))
    vt = TrendRules(weighting="vol_target", vol_target=0.35, max_weight_per_symbol=0.35, gross_cap=1.0)
    out.append(run_backtest(BacktestConfig(rules=vt, symbols=syms, label="A1 vol-target35% cap35% gross1.0"), data))
    vt_r75 = TrendRules(weighting="vol_target", vol_target=0.35, max_weight_per_symbol=0.35, gross_cap=1.0, risk_per_trade_pct=0.0075)
    out.append(run_backtest(BacktestConfig(rules=vt_r75, symbols=syms, label="A2 A1 + risk/trade 0.75%"), data))
    vt_r125 = TrendRules(weighting="vol_target", vol_target=0.35, max_weight_per_symbol=0.35, gross_cap=1.0, risk_per_trade_pct=0.0125)
    out.append(run_backtest(BacktestConfig(rules=vt_r125, symbols=syms, label="A3 A1 + risk/trade 1.25%"), data))
    out.append(run_backtest(BacktestConfig(rules=base, symbols=syms, fee_side_bp=0.0, slip_bp=1.0, label="A4 A0 Aster maker0 (+1bp slip)"), data))
    l1 = TrendRules(entry_rule="l1_score")
    out.append(run_backtest(BacktestConfig(rules=l1, symbols=syms, label="A5 A0 with l1_score>=3 entry (legacy live rule)"), data))
    out.append(run_backtest(BacktestConfig(rules=base, symbols=syms, exec_lag_days=0, label="A6 A0 exec lag 0 (live 00:05 UTC)"), data))
    pv35 = TrendRules(weighting="portfolio_vol_target", vol_target=0.35, max_weight_per_symbol=0.35, gross_cap=1.0)
    out.append(run_backtest(BacktestConfig(rules=pv35, symbols=syms, label="B1 bucket vol-target35% (cov60) cap35% gross1.0"), data))
    pv35_r = TrendRules(weighting="portfolio_vol_target", vol_target=0.35, max_weight_per_symbol=0.35, gross_cap=1.0, risk_per_trade_pct=0.0125)
    out.append(run_backtest(BacktestConfig(rules=pv35_r, symbols=syms, label="B2 B1 + risk/trade 1.25%"), data))
    pv35_r75 = TrendRules(weighting="portfolio_vol_target", vol_target=0.35, max_weight_per_symbol=0.35, gross_cap=1.0, risk_per_trade_pct=0.0075)
    out.append(run_backtest(BacktestConfig(rules=pv35_r75, symbols=syms, label="B3 B1 + risk/trade 0.75%"), data))
    pv35_g3 = TrendRules(weighting="portfolio_vol_target", vol_target=0.35, max_weight_per_symbol=0.35, gross_cap=3.0)
    out.append(run_backtest(BacktestConfig(rules=pv35_g3, symbols=syms, label="B4 B1 gross cap 3.0 (leverage allowed)"), data))
    return out


# ─────────────────────────── 今日目标仓位 ───────────────────────────

def exec_lag_days() -> int:
    """实盘执行延迟（天）。1 = 回测验证口径：在 bar t+1 收盘后执行 bar t 的决策（信号次日成交）。

    对照表 A6 显示 lag 0（收盘即执行）CAGR 48.9% / Sharpe 1.07 / MDD −57.5%，与验证过的 lag 1
    （55.9% / 1.15 / −50.6%）有差；两者差异主要来自少数大单的入场日，不构成"lag 0 更差"的结论，
    但要做到 trend_drift=0 就必须与回测同口径，所以默认 1，可用 TREND_EXEC_LAG_DAYS 调整。
    """
    try:
        return max(0, int(float(os.getenv("TREND_EXEC_LAG_DAYS", "1"))))
    except Exception:
        return 1


def target_positions_today(rules: Optional[TrendRules] = None, symbols: Optional[Sequence[str]] = None,
                           exchange: Optional[str] = None, data: Optional[Dict[str, pd.DataFrame]] = None,
                           lag: Optional[int] = None) -> Dict[str, Any]:
    """同一状态机跑到最后一根已收盘 bar → 每个核心币"现在应执行"的目标（持/空、权重、止损、指标）。

    lag（默认 exec_lag_days()）：取倒数第 1+lag 根已收盘 bar 的状态。lag=1 即回测口径：
    00:05 UTC 任务读到最后一根已收盘 bar 是 D−1，应执行的是 D−2 收盘时的决策。
    """
    r = rules or TrendRules.from_env()
    syms = list(symbols or core_symbols())
    ex = exchange or (os.getenv("TREND_DATA_EXCHANGE", "") or "binance")
    lag = exec_lag_days() if lag is None else max(0, int(lag))
    data = data or load_daily_ohlc(syms, ex)
    targets: Dict[str, Dict[str, Any]] = {}
    last_dates: List[pd.Timestamp] = []
    row_state: Dict[str, float] = {}
    row_rv: Dict[str, float] = {}
    row_sd: Dict[str, float] = {}
    recent_rets: Dict[str, pd.Series] = {}
    for s in syms:
        d = data.get(s)
        if d is None or len(d) < 260 + lag:
            targets[s] = {"target": 0, "reason": f"1d 数据不足({0 if d is None else len(d)}<{260 + lag})", "data_ok": False}
            continue
        ind = indicators(d, r)
        sig = entry_signal(d, r, ind)
        sm = run_state_machine(d, r, ind, sig)
        i = -1 - lag
        recent_rets[s] = ind["close"].pct_change(fill_method=None).iloc[-(r.vol_lookback + lag):len(d) - lag]
        st = int(sm.state.iloc[i])
        close = float(ind["close"].iloc[i])
        atr = float(ind["atr"].iloc[i]) if pd.notna(ind["atr"].iloc[i]) else None
        stop = float(sm.stop.iloc[i]) if pd.notna(sm.stop.iloc[i]) else None
        rv = float(ind["realized_vol"].iloc[i]) if pd.notna(ind["realized_vol"].iloc[i]) else None
        entry_close = float(sm.entry_close.iloc[i]) if pd.notna(sm.entry_close.iloc[i]) else None
        entry_stop = float(sm.entry_stop.iloc[i]) if pd.notna(sm.entry_stop.iloc[i]) else None
        init_sd = ((entry_close - entry_stop) / entry_close) if (entry_close and entry_stop and entry_close > 0) else (
            (r.chandelier_mult * atr / close) if (atr and close > 0) else None)
        last_dates.append(d.index[i])
        row_state[s] = float(st)
        row_rv[s] = rv if rv is not None else np.nan
        row_sd[s] = init_sd if init_sd is not None else np.nan
        targets[s] = {
            "target": st, "data_ok": True, "decision_bar": str(d.index[i].date()), "last_closed_bar": str(d.index[-1].date()),
            "close": close, "atr": atr, "chandelier_stop": stop, "realized_vol": rv,
            "signal": bool(sig.iloc[i]),
            "ema": {"fast": float(ind["e_fast"].iloc[i]), "mid": float(ind["e_mid"].iloc[i]),
                    "slow": float(ind["e_slow"].iloc[i]), "regime": float(ind["e_regime"].iloc[i])},
            "entry_close": entry_close, "initial_stop_distance_pct": init_sd,
            "reason": ("持有：入场规则成立且未破 Chandelier" if st else
                       ("空仓：入场规则不成立" if not bool(sig.iloc[i]) else "空仓：本 bar 刚出场/等待下一 bar 确认")),
        }
    weights: Dict[str, float] = {}
    if row_state:
        cols = list(row_state.keys())
        one = pd.DataFrame([row_state], columns=cols)
        rv_df = pd.DataFrame([row_rv], columns=cols)
        sd_df = pd.DataFrame([row_sd], columns=cols)
        if r.weighting == "portfolio_vol_target":
            # 单行权重需要当日协方差：用最近 vol_lookback 天的日收益直接算，再套同一函数
            rets = pd.DataFrame(recent_rets).reindex(columns=cols)
            cov = (rets.cov(min_periods=max(20, r.vol_lookback // 2)) * 365.0).to_numpy(dtype=float)
            from backend.services.trend_core import portfolio_vol_weights

            raw = portfolio_vol_weights(one.to_numpy(dtype=bool)[0], rv_df.to_numpy(dtype=float)[0], cov, r.vol_target)
            w = pd.DataFrame([raw], columns=cols).clip(upper=r.max_weight_per_symbol)
            if r.risk_per_trade_pct:
                rc = (r.risk_per_trade_pct / sd_df.where(sd_df > 0)).clip(upper=r.max_weight_per_symbol)
                w = pd.DataFrame(np.minimum(w.to_numpy(), rc.fillna(w).to_numpy()), columns=cols)
            g = float(w.iloc[0].sum())
            if g > r.gross_cap and g > 0:
                w = w * (r.gross_cap / g)
        else:
            w = target_weights(one, rv_df, r, stop_distance_pct=sd_df)
        for s in cols:
            weights[s] = float(w[s].iloc[0]) if s in w.columns else 0.0
            targets[s]["target_weight"] = weights[s]
    as_of = max(last_dates).date().isoformat() if last_dates else None
    return {"as_of_bar": as_of, "exec_lag_days": lag, "computed_at": datetime.now(timezone.utc).isoformat(), "exchange": ex,
            "rules": r.to_dict(), "symbols": syms, "targets": targets,
            "gross_weight": float(sum(weights.values())), "n_target_positions": int(sum(1 for v in row_state.values() if v > 0))}


# ─────────────────────────── 漂移对账 ───────────────────────────

def _kline_base(symbol: str) -> str:
    from backend.services.analysis.ledgers import _kline_base as kb

    return kb(symbol)


def compute_trend_drift(account_id: int, rules: Optional[TrendRules] = None, *, tier: str = "long",
                        weight_tol: float = 0.10, max_leverage: Optional[float] = None, persist: bool = True,
                        targets: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """规则今日应有仓位 vs 账户实际 long 车道持仓。

    drift = 缺仓（应持未持）+ 多仓（不应持却持 / 非核心币）+ 反向（long 车道有空单）；
    weight_drift = 已匹配币的 |实际名义/权益 − 目标权重| > weight_tol 的个数（金字塔加仓会高于目标，单独列出）；
    leverage_violations = 杠杆 > max_leverage 的仓位。

    [M2 2026-09-14] max_leverage 默认改为 env `TREND_DRIFT_MAX_LEVERAGE`（默认 5.0）：
    旧硬编码 3.0 写于 9/4 币种杠杆权威之前——权威表现行 BTC/ETH 5x、二线 4x、小币 3x，
    3.0 会把 E1 所有正常仓位误判成杠杆违规（F4 门永远过不了的自锁）。5.0 = 权威表上限。
    """
    if max_leverage is None:
        try:
            max_leverage = float(os.getenv("TREND_DRIFT_MAX_LEVERAGE", "5.0") or 5.0)
        except (TypeError, ValueError):
            max_leverage = 5.0
    from sqlalchemy import text
    from backend.core.tenant import set_system_identity
    from backend.database.connection import SessionLocal

    r = rules or TrendRules.from_env()
    tg = targets or target_positions_today(r)
    set_system_identity()
    db = SessionLocal()
    try:
        eq_row = db.execute(text("SELECT total_equity FROM paper_balances WHERE account_id = :a"), {"a": account_id}).first()
        equity = float(eq_row[0]) if eq_row and eq_row[0] is not None else 0.0
        rows = db.execute(text(
            "SELECT id, symbol, side, size, entry_price, mark_price, leverage, margin, sl_price, add_count, opened_at "
            "FROM paper_positions WHERE account_id = :a AND status = 'open' AND timeframe_tier = :t"),
            {"a": account_id, "t": tier}).mappings().all()
    finally:
        db.close()
    actual: Dict[str, Dict[str, Any]] = {}
    wrong_side: List[Dict[str, Any]] = []
    lev_viol: List[Dict[str, Any]] = []
    for p in rows:
        base = _kline_base(p["symbol"])
        notional = float(p["size"] or 0) * float(p["mark_price"] or p["entry_price"] or 0)
        rec = {"id": p["id"], "symbol": p["symbol"], "side": p["side"], "notional": notional,
               "weight": (notional / equity) if equity > 0 else None, "leverage": float(p["leverage"] or 0),
               "sl_price": p["sl_price"], "add_count": p["add_count"], "opened_at": str(p["opened_at"])}
        if str(p["side"]).lower() != "long":
            wrong_side.append(rec)
            continue
        if rec["leverage"] > max_leverage:
            lev_viol.append(rec)
        actual[base] = rec
    core = set(tg["symbols"])
    tmap = tg["targets"]
    missing = [s for s in tg["symbols"] if tmap.get(s, {}).get("target") == 1 and s not in actual]
    extra = [rec for s, rec in actual.items() if (s not in core) or tmap.get(s, {}).get("target", 0) == 0]
    matched: List[Dict[str, Any]] = []
    weight_drift: List[Dict[str, Any]] = []
    for s, rec in actual.items():
        if s in core and tmap.get(s, {}).get("target") == 1:
            tw = float(tmap[s].get("target_weight") or 0.0)
            aw = rec["weight"]
            dev = (aw - tw) if aw is not None else None
            item = {"symbol": s, "actual_weight": aw, "target_weight": tw, "deviation": dev,
                    "target_stop": tmap[s].get("chandelier_stop"), "actual_sl": rec["sl_price"], "add_count": rec["add_count"]}
            matched.append(item)
            if dev is not None and abs(dev) > weight_tol:
                weight_drift.append(item)
    drift = len(missing) + len(extra) + len(wrong_side)
    out = {
        "account_id": account_id, "tier": tier, "as_of_bar": tg.get("as_of_bar"), "computed_at": tg.get("computed_at"),
        "equity": equity, "trend_drift": drift, "weight_drift": len(weight_drift), "leverage_violations": len(lev_viol),
        "missing": missing, "extra": extra, "wrong_side": wrong_side, "matched": matched,
        "weight_drift_detail": weight_drift, "leverage_violation_detail": lev_viol,
        "target_positions": {s: tmap[s].get("target") for s in tg["symbols"]},
        "target_weights": {s: tmap[s].get("target_weight") for s in tg["symbols"] if tmap[s].get("data_ok")},
        "rules": tg.get("rules"),
    }
    if persist:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(os.path.join(DATA_DIR, "latest.json"), "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2, default=str)
            with open(os.path.join(DATA_DIR, "history.jsonl"), "a", encoding="utf-8") as f:
                slim = {k: out[k] for k in ("account_id", "as_of_bar", "computed_at", "equity", "trend_drift",
                                             "weight_drift", "leverage_violations", "missing", "target_positions")}
                slim["extra"] = [e["symbol"] for e in extra]
                slim["wrong_side"] = [e["symbol"] for e in wrong_side]
                f.write(json.dumps(slim, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:  # pragma: no cover
            logger.warning("[trend_drift] 落盘失败: %s", exc)
    return out


def load_latest_drift() -> Optional[Dict[str, Any]]:
    p = os.path.join(DATA_DIR, "latest.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def drift_history(limit: int = 60) -> List[Dict[str, Any]]:
    p = os.path.join(DATA_DIR, "history.jsonl")
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            lines = f.readlines()[-int(limit):]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:
        return []


# ─────────────────────────── CLI ───────────────────────────

def _main() -> int:
    ap = argparse.ArgumentParser(description="E1 趋势 sleeve 回测 / 目标仓位 / 漂移对账")
    ap.add_argument("--symbols", default=",".join(core_symbols()))
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--fee-bp", type=float, default=5.0)
    ap.add_argument("--slip-bp", type=float, default=2.0)
    ap.add_argument("--funding", type=float, default=0.075)
    ap.add_argument("--lag", type=int, default=1)
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--entry-rule", default="ema_stack", choices=["ema_stack", "l1_score"])
    ap.add_argument("--chandelier", type=float, default=3.0)
    ap.add_argument("--atr", type=int, default=20)
    ap.add_argument("--weighting", default="equal", choices=["equal", "vol_target"])
    ap.add_argument("--vol-target", type=float, default=0.35)
    ap.add_argument("--cap", type=float, default=0.35)
    ap.add_argument("--gross", type=float, default=1.0)
    ap.add_argument("--risk-per-trade", type=float, default=None)
    ap.add_argument("--variants", action="store_true", help="跑标准对照表")
    ap.add_argument("--targets", action="store_true", help="打印今日目标仓位（TrendRules.from_env）")
    ap.add_argument("--drift", type=int, default=None, help="对指定账户做漂移对账")
    ap.add_argument("--json", default=None, help="结果写入 JSON 文件")
    a = ap.parse_args()
    syms = tuple(s.strip().upper() for s in a.symbols.split(",") if s.strip())
    t0 = time.time()
    payload: Dict[str, Any] = {}
    if a.targets:
        tg = target_positions_today(symbols=syms, exchange=a.exchange)
        payload["targets"] = tg
        print(f"as_of_bar={tg['as_of_bar']} gross={tg['gross_weight']:.2f} n={tg['n_target_positions']}")
        for s, t in tg["targets"].items():
            print(f"  {s:6s} target={t.get('target')} w={t.get('target_weight', 0) or 0:.3f} close={t.get('close')} "
                  f"stop={t.get('chandelier_stop')} rv={t.get('realized_vol')} sig={t.get('signal')} | {t.get('reason')}")
    if a.drift is not None:
        d = compute_trend_drift(a.drift)
        payload["drift"] = d
        print(f"account {a.drift}: trend_drift={d['trend_drift']} weight_drift={d['weight_drift']} "
              f"leverage_violations={d['leverage_violations']} missing={d['missing']} "
              f"extra={[e['symbol'] for e in d['extra']]} wrong_side={[e['symbol'] for e in d['wrong_side']]}")
    if a.variants:
        data = load_daily_ohlc(syms, a.exchange)
        res = standard_variants(data, syms)
        payload["variants"] = [x.to_dict() for x in res]
        for x in res:
            print(x.summary_line())
        print(f"reference check (A0): {res[0].reference_check}")
    if not (a.targets or a.drift is not None or a.variants):
        rules = TrendRules(entry_rule=a.entry_rule, chandelier_mult=a.chandelier, atr_period=a.atr, weighting=a.weighting,
                           vol_target=a.vol_target, max_weight_per_symbol=a.cap, gross_cap=a.gross, risk_per_trade_pct=a.risk_per_trade)
        cfg = BacktestConfig(rules=rules, symbols=syms, start=a.start, end=a.end, fee_side_bp=a.fee_bp, slip_bp=a.slip_bp,
                             funding_ann=a.funding, exec_lag_days=a.lag, exchange=a.exchange,
                             label=f"E1 {a.weighting} {a.entry_rule} ch{a.chandelier}xATR{a.atr}")
        res = run_backtest(cfg)
        payload["backtest"] = res.to_dict()
        print(res.summary_line())
        print(f"BTC buy&hold: CAGR {res.benchmark_btc.get('cagr', 0) * 100:.1f}% Sharpe {res.benchmark_btc.get('sharpe', 0):.2f} "
              f"MDD {res.benchmark_btc.get('mdd', 0) * 100:.1f}%")
        print(f"reference check: {res.reference_check}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        print(f"written {a.json}")
    print(f"done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
