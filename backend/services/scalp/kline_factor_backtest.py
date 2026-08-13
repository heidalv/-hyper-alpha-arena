"""K线因子研究回测（M1 真正的主线，不用旧 signal_log 复盘）。

用历史 5m K线直接计算一组透明因子（动量/RSI/波动/量能/位置），
滚动 z-score 合成方向分，按阈值出信号，逐仓模拟三重障碍退出，
扣 taker fee + 滑点 + funding，输出：
- 笔数/胜率/平均净收益/PF
- 分数分桶
- 前后半段稳定性对照（时间切分）
- 随机方向基准 permutation p

设计意图：旧 signal_log 是旧策略自己的产物，回放它只是“复盘垃圾”；
本脚本直接从 K线出发，才能回答“这组因子到底有没有边缘”。

用法（仓库根目录）：
    python -m backend.services.scalp.kline_factor_backtest --symbols BTC,ETH --days 60
    python -m backend.services.scalp.kline_factor_backtest --symbols BTC --scenario pessimistic
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]

SCENARIOS = {
    "realistic": {"fee_bps": 0.5, "slip_bps": 2.97},
    "pessimistic": {"fee_bps": 0.5, "slip_bps": 79.26},
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _round(v: Any, nd: int = 4) -> Any:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, nd)
    except Exception:
        return None


def load_klines(symbols: List[str], days: int, exchange: Optional[str] = None,
                period: str = "5m") -> Dict[str, pd.DataFrame]:
    from backend.database.connection import MarketSessionLocal
    from backend.core.tenant import system_identity

    now = _now_utc()
    start = int((now - timedelta(days=days)).timestamp())
    end = int(now.timestamp())
    ph = ",".join(":s%d" % i for i in range(len(symbols)))
    params: Dict[str, Any] = {"start": start, "end": end}
    params.update({"s%d" % i: s for i, s in enumerate(symbols)})
    ex_clause = ""
    if exchange:
        params["ex"] = exchange
        ex_clause = " AND exchange = :ex"
    out: Dict[str, pd.DataFrame] = {}
    with system_identity():
        with MarketSessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT symbol, timestamp, open_price, high_price, low_price, close_price, volume "
                    "FROM crypto_klines WHERE period = :period AND symbol IN (%s)%s "
                    "AND timestamp >= :start AND timestamp <= :end "
                    "ORDER BY symbol, timestamp" % (ph, ex_clause)
                ),
                {"period": period, **params},
            ).mappings().all()
    by_sym: Dict[str, Dict[int, tuple]] = {}
    for r in rows:
        sym = str(r["symbol"])
        ts = int(r["timestamp"])
        try:
            by_sym.setdefault(sym, {})[ts] = (
                float(r["open_price"]), float(r["high_price"]),
                float(r["low_price"]), float(r["close_price"]),
                float(r["volume"] or 0.0),
            )
        except Exception:
            continue
    for sym, d in by_sym.items():
        ts_sorted = sorted(d.keys())
        arr = np.array([d[t] for t in ts_sorted], dtype=np.float64)
        out[sym] = pd.DataFrame(
            arr,
            columns=["open", "high", "low", "close", "volume"],
            index=pd.Index(ts_sorted, name="ts"),
        )
    return out


def compute_factors(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]
    volume = df["volume"].replace(0, np.nan)
    ret = close.pct_change()
    out = pd.DataFrame(index=df.index)
    out["ret1"] = ret
    out["mom5"] = close.pct_change(5)
    out["mom10"] = close.pct_change(10)
    out["mom20"] = close.pct_change(20)
    # RSI14（Wilder 近似）
    up = ret.clip(lower=0.0)
    down = (-ret).clip(lower=0.0)
    up_ewm = up.ewm(alpha=1.0 / 14.0, min_periods=14).mean()
    down_ewm = down.ewm(alpha=1.0 / 14.0, min_periods=14).mean()
    rs = up_ewm / down_ewm.replace(0, np.nan)
    out["rsi14"] = 100.0 - 100.0 / (1.0 + rs)
    out["vol20"] = ret.rolling(20).std()
    out["vol_ratio"] = volume / volume.rolling(20).mean()
    out["volume_z"] = (volume - volume.rolling(20).mean()) / volume.rolling(20).std()
    out["zscore20"] = (close - close.rolling(20).mean()) / close.rolling(20).std()
    rng_lo = df["low"].rolling(20).min()
    rng_hi = df["high"].rolling(20).max()
    out["range_pos"] = (close - rng_lo) / (rng_hi - rng_lo).replace(0, np.nan)
    out["decay_ret"] = ret.ewm(alpha=0.2, min_periods=20).mean()
    out["wick_ratio"] = (df["high"] - df["low"]) / close.replace(0, np.nan)
    return out


def rolling_z(df: pd.DataFrame, window: int = 120) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for c in df.columns:
        s = df[c]
        mean = s.rolling(window, min_periods=60).mean()
        std = s.rolling(window, min_periods=60).std()
        out[c] = (s - mean) / std.replace(0, np.nan)
    return out


def composite_score(factors: pd.DataFrame, window: int = 120,
                    factor_set: str = "hybrid") -> pd.DataFrame:
    z = rolling_z(factors, window)
    # 等权 + 方向一致的简单加权（可后续替换为 IC 加权）
    z = z.fillna(0.0)
    if factor_set == "meanrev":
        weights = {
            "zscore20": 1.0, "rsi14": 0.8, "range_pos": 0.6, "decay_ret": 0.5,
        }
        direction_mult = -1.0
    elif factor_set == "breakout":
        weights = {
            "mom5": 1.0, "mom10": 1.0, "mom20": 1.0,
            "vol_ratio": 0.6, "volume_z": 0.5, "range_pos": 0.8, "decay_ret": 1.0,
        }
        direction_mult = 1.0
    else:
        weights = {
            "mom5": 1.0, "mom10": 1.0, "mom20": 1.0,
            "rsi14": 0.5, "zscore20": 1.0, "range_pos": 0.5,
            "decay_ret": 1.0,
        }
        direction_mult = 1.0
    num = pd.Series(0.0, index=z.index)
    denom = 0.0
    for c, w in weights.items():
        if c in z.columns:
            num = num + w * z[c]
            denom += w * w
    comp = (num / math.sqrt(denom) if denom else num) * direction_mult
    score = (100.0 * (1.0 - np.exp(-np.abs(comp)))).clip(0, 100)
    direction = np.sign(comp)
    return pd.DataFrame({"composite": comp, "score": score, "direction": direction},
                        index=z.index)


def simulate_trade(df: pd.DataFrame, entry_idx: int, direction: float,
                   sl_pct: float, tp_pct: float, max_hold_candles: int,
                   round_trip_cost: float, funding_bps_per_hour: float) -> Dict[str, Any]:
    entry = float(df["open"].iloc[entry_idx])
    last_idx = min(entry_idx + max_hold_candles, len(df) - 1)
    exit_price = float(df["close"].iloc[last_idx])
    exit_idx = last_idx
    reason = "timeout"
    for j in range(entry_idx, last_idx + 1):
        hi = float(df["high"].iloc[j])
        lo = float(df["low"].iloc[j])
        if direction > 0:
            if lo <= entry * (1.0 - sl_pct):
                exit_price = entry * (1.0 - sl_pct)
                exit_idx = j
                reason = "sl"
                break
            if hi >= entry * (1.0 + tp_pct):
                exit_price = entry * (1.0 + tp_pct)
                exit_idx = j
                reason = "tp"
                break
        else:
            if hi >= entry * (1.0 + sl_pct):
                exit_price = entry * (1.0 + sl_pct)
                exit_idx = j
                reason = "sl"
                break
            if lo <= entry * (1.0 - tp_pct):
                exit_price = entry * (1.0 - tp_pct)
                exit_idx = j
                reason = "tp"
                break
    hold_hours = (exit_idx - entry_idx) * 5.0 / 60.0
    funding = funding_bps_per_hour / 1e4 * hold_hours
    net = direction * (exit_price / entry - 1.0) - round_trip_cost - funding
    return {
        "entry_idx": entry_idx,
        "exit_idx": exit_idx,
        "reason": reason,
        "net_ret": net,
        "hold_hours": hold_hours,
    }


def run_symbol(df: pd.DataFrame, threshold: float, sl_pct: float, tp_pct: float,
               max_hold_candles: int, round_trip_cost: float, cooldown_candles: int,
               warmup: int, z_window: int, funding_bps_per_hour: float,
               symbol: str, factor_set: str = "hybrid") -> List[Dict[str, Any]]:
    factors = compute_factors(df)
    sig = composite_score(factors, z_window, factor_set)
    trades: List[Dict[str, Any]] = []
    i = warmup
    n = len(df)
    while i < n - 2:
        comp = float(sig["composite"].iloc[i])
        score = float(sig["score"].iloc[i])
        direction = float(sig["direction"].iloc[i])
        if direction == 0 or abs(comp) < threshold:
            i += 1
            continue
        entry_idx = i + 1
        sim = simulate_trade(
            df, entry_idx, direction, sl_pct, tp_pct,
            max_hold_candles, round_trip_cost, funding_bps_per_hour,
        )
        trades.append({
            "symbol": symbol,
            "signal_ts": int(df.index[i]),
            "entry_ts": int(df.index[entry_idx]),
            "score": score,
            "composite": comp,
            "direction": direction,
            **sim,
        })
        i = sim["exit_idx"] + cooldown_candles + 1
    return trades


def _bucket(score: float) -> str:
    if score < 30:
        return "<30"
    if score < 40:
        return "30-40"
    if score < 50:
        return "40-50"
    if score < 60:
        return "50-60"
    return ">=60"


def _aggregate(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"n": 0, "win_rate": 0.0, "avg_net_ret": 0.0, "profit_factor": None}
    rets = [t["net_ret"] for t in trades]
    wins = sum(1 for r in rets if r > 0)
    gross_win = sum(r for r in rets if r > 0)
    gross_loss = sum(r for r in rets if r < 0)
    return {
        "n": len(trades),
        "win_rate": round(wins / len(trades), 4),
        "avg_net_ret": round(sum(rets) / len(trades), 6),
        "profit_factor": round(gross_win / abs(gross_loss), 4) if gross_loss else None,
    }


def _stats(trades: List[Dict[str, Any]], n_perm: int = 200) -> Dict[str, Any]:
    """聚合指标 + t 统计 + bootstrap 95% CI + 组内随机方向基准。"""
    base = _aggregate(trades)
    if not trades:
        base.update({"t_stat": None, "ci95_lo": None, "ci95_hi": None,
                     "permutation_p": 1.0})
        return base
    rets = np.array([t["net_ret"] for t in trades], dtype=np.float64)
    n = len(rets)
    mean = float(rets.mean())
    sd = float(rets.std(ddof=1)) if n > 1 else 0.0
    t_stat = mean / (sd / math.sqrt(n)) if sd > 0 else None
    rng = np.random.default_rng(42)
    boot = np.array([
        float(rng.choice(rets, size=n, replace=True).mean())
        for _ in range(1000)
    ])
    lo, hi = float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
    base.update({
        "t_stat": _round(t_stat, 4),
        "ci95_lo": _round(lo, 6),
        "ci95_hi": _round(hi, 6),
        "permutation_p": _permutation_p(trades, n_perm),
    })
    return base


def _permutation_p(trades: List[Dict[str, Any]], n_perm: int, seed: int = 42) -> float:
    if not trades:
        return 1.0
    n = len(trades)
    actual = sum(t["net_ret"] for t in trades) / n
    dirs = [1.0 if t["direction"] > 0 else -1.0 for t in trades]
    moves = [abs(t["net_ret"]) for t in trades]
    rng = random.Random(seed)
    count = 0
    for _ in range(n_perm):
        rng.shuffle(dirs)
        perm_avg = sum(s * m for s, m in zip(dirs, moves)) / n
        if perm_avg >= actual:
            count += 1
    return round((count + 1.0) / (n_perm + 1.0), 4)


def _bucket_stats(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[float]] = {}
    for t in trades:
        groups.setdefault(_bucket(t["score"]), []).append(t["net_ret"])
    out = []
    for b in ["<30", "30-40", "40-50", "50-60", ">=60"]:
        rets = groups.get(b, [])
        if not rets:
            continue
        out.append({
            "bucket": b,
            "n": len(rets),
            "win_rate": round(sum(1 for r in rets if r > 0) / len(rets), 4),
            "avg_net_ret": round(sum(rets) / len(rets), 6),
        })
    return out


def run_kline_factor_backtest(
    symbols: List[str],
    days: int = 60,
    exchange: Optional[str] = "asterdex",
    period: str = "5m",
    threshold: float = 1.0,
    sl_pct: float = 0.005,
    tp_pct: float = 0.01,
    max_hold_candles: int = 24,
    fee_bps: float = 0.5,
    slip_bps: float = 2.97,
    funding_bps_per_hour: float = 0.0,
    cooldown_candles: int = 6,
    warmup: int = 120,
    z_window: int = 120,
    n_perm: int = 200,
    scenario: str = "realistic",
    factor_set: str = "hybrid",
    save_report: bool = True,
) -> Dict[str, Any]:
    klines = load_klines(symbols, days, exchange, period=period)
    round_trip_cost = (fee_bps * 2.0 + slip_bps * 2.0) / 1e4
    trades: List[Dict[str, Any]] = []
    per_symbol: Dict[str, Dict[str, Any]] = {}
    for sym, df in klines.items():
        st = run_symbol(
            df, threshold, sl_pct, tp_pct, max_hold_candles,
            round_trip_cost, cooldown_candles, warmup, z_window,
            funding_bps_per_hour, sym,
            factor_set=factor_set,
        )
        st_sorted = sorted(st, key=lambda t: t["signal_ts"])
        h = len(st_sorted) // 2
        per_symbol[sym] = {
            "total": _stats(st_sorted, n_perm),
            "older_half": _stats(st_sorted[:h], n_perm) if h else {"n": 0},
            "newer_half": _stats(st_sorted[h:], n_perm) if st_sorted else {"n": 0},
        }
        trades.extend(st)

    trades.sort(key=lambda t: t["signal_ts"])
    half = len(trades) // 2
    older = _stats(trades[:half], n_perm) if half else {"n": 0}
    newer = _stats(trades[half:], n_perm) if trades else {"n": 0}
    report = {
        "generated_at": _now_utc().isoformat(),
        "mode": "kline_factor",
        "factor_set": factor_set,
        "scenario": scenario,
        "days": days,
        "exchange": exchange,
        "period": period,
        "symbols": symbols,
        "config": {
            "threshold": threshold,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "max_hold_candles": max_hold_candles,
            "cooldown_candles": cooldown_candles,
            "warmup": warmup,
            "z_window": z_window,
            "fee_bps": fee_bps,
            "slip_bps": slip_bps,
            "funding_bps_per_hour": funding_bps_per_hour,
            "round_trip_cost_bps": round(round_trip_cost * 1e4, 4),
        },
        "n_signals": len(trades),
        "total": _stats(trades, n_perm),
        "older_half": older,
        "newer_half": newer,
        "permutation_p": _permutation_p(trades, n_perm),
        "buckets": _bucket_stats(trades),
        "per_symbol": per_symbol,
        "parity_score": None,
    }
    out_dir = _ROOT / "reports" / "scalp_backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (
        "kline_factor_%s_%s_d%d.json"
        % (_now_utc().strftime("%Y-%m-%d"), scenario, days)
    )
    if save_report:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("K线因子回测完成: %s", path)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="K线因子研究回测")
    ap.add_argument("--symbols", default="BTC,ETH", help="逗号分隔")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--exchange", default="asterdex")
    ap.add_argument("--period", default="5m", choices=["5m", "15m", "1h", "4h", "1d"])
    ap.add_argument("--threshold", type=float, default=1.0)
    ap.add_argument("--sl-pct", type=float, default=0.005)
    ap.add_argument("--tp-pct", type=float, default=0.01)
    ap.add_argument("--max-hold-candles", type=int, default=24)
    ap.add_argument("--fee-bps", type=float, default=None)
    ap.add_argument("--slip-bps", type=float, default=None)
    ap.add_argument("--funding-bps-per-hour", type=float, default=0.0)
    ap.add_argument("--cooldown-candles", type=int, default=6)
    ap.add_argument("--warmup", type=int, default=120)
    ap.add_argument("--z-window", type=int, default=120)
    ap.add_argument("--permutations", type=int, default=200)
    ap.add_argument("--scenario", choices=list(SCENARIOS.keys()), default="realistic")
    ap.add_argument("--factor-set", choices=["hybrid", "meanrev", "breakout"], default="hybrid")
    args = ap.parse_args()

    cfg = SCENARIOS[args.scenario]
    report = run_kline_factor_backtest(
        symbols=[s.strip().upper() for s in args.symbols.split(",") if s.strip()],
        days=args.days,
        exchange=args.exchange,
        period=args.period,
        threshold=args.threshold,
        sl_pct=args.sl_pct,
        tp_pct=args.tp_pct,
        max_hold_candles=args.max_hold_candles,
        fee_bps=args.fee_bps if args.fee_bps is not None else cfg["fee_bps"],
        slip_bps=args.slip_bps if args.slip_bps is not None else cfg["slip_bps"],
        funding_bps_per_hour=args.funding_bps_per_hour,
        cooldown_candles=args.cooldown_candles,
        warmup=args.warmup,
        z_window=args.z_window,
        n_perm=args.permutations,
        scenario=args.scenario,
        factor_set=args.factor_set,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
