"""短线真实信号回放回测（M1 v0）。

不做“幻想回测”：只用 scalp_signal_log 里真实记录过的信号 + alpha_market 真实 5m K 线，
按两种口径结算：
- fixed：固定前瞻窗口（与 signal_logger 口径一致）
- triple_barrier：SL/TP/超时（与纸盘退出逻辑一致）

成本：taker fee + 滑点（默认取成本审计 P95 绝对值）+ 可选 funding。
随机基准：打乱方向标签 N 次，输出 permutation p 值。

用法（仓库根目录）：
    python -m backend.services.scalp.scalp_strategy_backtest --days 30
    python -m backend.services.scalp.scalp_strategy_backtest --scenario pessimistic
"""
from __future__ import annotations

import argparse
import bisect
import json
import logging
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]

SCENARIOS = {
    "realistic": {"fee_bps": 0.5, "slip_bps": 2.97, "funding_bps_per_hour": 0.0},
    "pessimistic": {"fee_bps": 0.5, "slip_bps": 79.26, "funding_bps_per_hour": 0.0},
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _load_signals(days: int, dedup_sec: int, offset_days: int = 0) -> List[Dict[str, Any]]:
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    start = int((_now_utc() - timedelta(days=days + offset_days)).timestamp())
    end = int((_now_utc() - timedelta(days=offset_days)).timestamp())
    rows: List[Dict[str, Any]] = []
    with system_identity():
        with SessionLocal() as db:
            res = db.execute(
                text(
                    "SELECT symbol, signal_ts, direction, factor_score, entry_price "
                    "FROM scalp_signal_log "
                    "WHERE settled = TRUE AND win IS NOT NULL "
                    "AND entry_price IS NOT NULL AND entry_price > 0 "
                    "AND direction IN ('long','short') "
                    "AND signal_ts >= :start AND signal_ts < :end "
                    "ORDER BY signal_ts ASC"
                ),
                {"start": start, "end": end},
            ).mappings().all()
    last_kept: Dict[str, int] = {}
    for r in res:
        sym = str(r["symbol"])
        ts = int(r["signal_ts"])
        if sym in last_kept and ts - last_kept[sym] < dedup_sec:
            continue
        last_kept[sym] = ts
        rows.append({
            "symbol": sym,
            "ts": ts,
            "dir": 1.0 if r["direction"] == "long" else -1.0,
            "score": float(r["factor_score"] or 0.0),
            "entry": float(r["entry_price"]),
        })
    return rows


def _load_klines(symbols: List[str], start_ts: int, end_ts: int,
                 exchange: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    from backend.database.connection import MarketSessionLocal
    from backend.core.tenant import system_identity

    out: Dict[str, Dict[str, Any]] = {}
    if not symbols:
        return out
    placeholders = ",".join(":s%d" % i for i in range(len(symbols)))
    params = {"start": start_ts, "end": end_ts}
    params.update({"s%d" % i: s for i, s in enumerate(symbols)})
    by_sym: Dict[str, Dict[str, List[float]]] = {}

    def _query(ex_filter: bool, syms: List[str]) -> List[Any]:
        ph = ",".join(":s%d" % i for i in range(len(syms)))
        p = {"start": start_ts, "end": end_ts}
        p.update({"s%d" % i: s for i, s in enumerate(syms)})
        if ex_filter and exchange:
            p["ex"] = exchange
            return db.execute(
                text(
                    "SELECT symbol, timestamp, high_price, low_price, close_price "
                    "FROM crypto_klines "
                    "WHERE period = '5m' AND exchange = :ex AND symbol IN (%s) "
                    "AND timestamp >= :start AND timestamp <= :end "
                    "ORDER BY symbol, timestamp" % ph
                ),
                p,
            ).mappings().all()
        return db.execute(
            text(
                "SELECT symbol, timestamp, high_price, low_price, close_price "
                "FROM crypto_klines "
                "WHERE period = '5m' AND symbol IN (%s) "
                "AND timestamp >= :start AND timestamp <= :end "
                "ORDER BY symbol, timestamp" % ph
            ),
            p,
        ).mappings().all()

    with system_identity():
        with MarketSessionLocal() as db:
            res = _query(True, symbols)
            loaded_syms = {str(r["symbol"]) for r in res}
            missing = [s for s in symbols if s not in loaded_syms]
            if missing:
                res += _query(False, missing)
    for r in res:
        sym = str(r["symbol"])
        try:
            ts = int(r["timestamp"])
            if sym not in by_sym:
                by_sym[sym] = {}
            # 多交易所同时间戳去重：保留最后一行（与数据落库顺序一致）
            by_sym[sym][ts] = (
                float(r["high_price"] or 0.0),
                float(r["low_price"] or 0.0),
                float(r["close_price"] or 0.0),
            )
        except Exception:
            continue
    for sym, d in by_sym.items():
        if not d:
            continue
        ts_sorted = sorted(d.keys())
        out[sym] = {
            "ts": ts_sorted,
            "high": [d[t][0] for t in ts_sorted],
            "low": [d[t][1] for t in ts_sorted],
            "close": [d[t][2] for t in ts_sorted],
        }
    out["_exchange_used"] = exchange
    return out


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


def _simulate(signal: Dict[str, Any], klines: Optional[Dict[str, Any]],
              horizon_sec: int, sl_pct: float, tp_pct: float,
              max_hold_sec: int, round_trip_cost: float) -> Dict[str, Any]:
    sym = signal["symbol"]
    if not klines or not klines.get("ts"):
        return {}
    ts_list = klines["ts"]
    entry_ts = signal["ts"]
    entry_bucket = (entry_ts // 300) * 300
    start_idx = bisect.bisect_right(ts_list, entry_bucket)
    if start_idx >= len(ts_list):
        return {}
    dir_sign = signal["dir"]
    entry = signal["entry"]

    def _close_at_or_after(t: int) -> float:
        idx = bisect.bisect_left(ts_list, t)
        if idx >= len(ts_list):
            idx = len(ts_list) - 1
        return klines["close"][idx]

    # 口径 1：固定前瞻窗口
    close_fixed = _close_at_or_after(entry_ts + horizon_sec)
    raw_fixed = dir_sign * (close_fixed / entry - 1.0)
    # 单位/脏数据防护：单笔超过 ±100% 视为数据异常，不计入
    if abs(raw_fixed) > 1.0:
        return {"anomaly": True}
    ret_fixed = raw_fixed - round_trip_cost

    # 口径 2：三重障碍
    exit_price = None
    reason = "timeout"
    last_close = klines["close"][start_idx]
    for i in range(start_idx, len(ts_list)):
        ts = ts_list[i]
        if ts > entry_ts + max_hold_sec:
            break
        hi = klines["high"][i]
        lo = klines["low"][i]
        last_close = klines["close"][i]
        if dir_sign > 0:
            if lo <= entry * (1.0 - sl_pct):
                exit_price = entry * (1.0 - sl_pct)
                reason = "sl"
                break
            if hi >= entry * (1.0 + tp_pct):
                exit_price = entry * (1.0 + tp_pct)
                reason = "tp"
                break
        else:
            if hi >= entry * (1.0 + sl_pct):
                exit_price = entry * (1.0 + sl_pct)
                reason = "sl"
                break
            if lo <= entry * (1.0 - tp_pct):
                exit_price = entry * (1.0 - tp_pct)
                reason = "tp"
                break
    if exit_price is None:
        exit_price = last_close
    raw_tb = dir_sign * (exit_price / entry - 1.0)
    if abs(raw_tb) > 1.0:
        return {"anomaly": True}
    ret_tb = raw_tb - round_trip_cost

    return {
        "symbol": sym,
        "ts": entry_ts,
        "score": signal["score"],
        "dir": dir_sign,
        "ret_fixed": ret_fixed,
        "ret_tb": ret_tb,
        "move_abs_fixed": abs(dir_sign * (close_fixed / entry - 1.0)),
        "move_abs_tb": abs(dir_sign * (exit_price / entry - 1.0)),
        "reason_tb": reason,
    }


def _aggregate(trades: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    n = len(trades)
    if not n:
        return {"n": 0, "win_rate": 0.0, "avg_net_ret": 0.0, "profit_factor": None}
    rets = [t[key] for t in trades]
    wins = sum(1 for r in rets if r > 0)
    gross_win = sum(r for r in rets if r > 0)
    gross_loss = sum(r for r in rets if r < 0)
    return {
        "n": n,
        "win_rate": round(wins / n, 4),
        "avg_net_ret": round(sum(rets) / n, 6),
        "profit_factor": round(gross_win / abs(gross_loss), 4) if gross_loss else None,
        "gross_win": round(gross_win, 6),
        "gross_loss": round(gross_loss, 6),
    }


def _permutation_p(trades: List[Dict[str, Any]], key: str,
                   n_perm: int, seed: int = 42) -> float:
    if not trades:
        return 1.0
    n = len(trades)
    actual = sum(t[key] for t in trades) / n
    move_key = "move_abs_fixed" if key == "ret_fixed" else "move_abs_tb"
    dirs = [1.0 if t["dir"] > 0 else -1.0 for t in trades]
    moves = [t[move_key] for t in trades]
    rng = random.Random(seed)
    count = 0
    for _ in range(n_perm):
        rng.shuffle(dirs)
        perm_avg = sum(s * a for s, a in zip(dirs, moves)) / n
        if perm_avg >= actual:
            count += 1
    return round((count + 1.0) / (n_perm + 1.0), 4)


def _bucket_stats(trades: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    groups: Dict[str, List[float]] = {}
    for t in trades:
        b = _bucket(t["score"])
        groups.setdefault(b, []).append(t[key])
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


def run_scalp_backtest(
    days: int = 30,
    horizon_sec: int = 1800,
    sl_pct: float = 0.005,
    tp_pct: float = 0.01,
    max_hold_sec: int = 7200,
    fee_bps: float = 0.5,
    slip_bps: float = 2.97,
    funding_bps_per_hour: float = 0.0,
    dedup_sec: int = 1800,
    n_perm: int = 200,
    scenario: str = "realistic",
    exchange: Optional[str] = None,
    offset_days: int = 0,
) -> Dict[str, Any]:
    signals = _load_signals(days, dedup_sec, offset_days)
    if not signals:
        return {"error": "no signals", "n": 0}
    min_ts = min(s["ts"] for s in signals) - 3600
    max_ts = max(s["ts"] for s in signals) + max_hold_sec + horizon_sec + 3600
    symbols = sorted({s["symbol"] for s in signals})
    klines = _load_klines(symbols, min_ts, max_ts, exchange=exchange)
    round_trip_cost = (fee_bps * 2.0 + slip_bps * 2.0) / 1e4
    trades = []
    skipped = 0
    n_anomalies = 0
    for sig in signals:
        sim = _simulate(
            sig,
            klines.get(sig["symbol"]),
            horizon_sec,
            sl_pct,
            tp_pct,
            max_hold_sec,
            round_trip_cost,
        )
        if sim.get("anomaly"):
            n_anomalies += 1
            continue
        if not sim:
            skipped += 1
            continue
        trades.append(sim)

    report = {
        "generated_at": _now_utc().isoformat(),
        "scenario": scenario,
        "days": days,
        "config": {
            "horizon_sec": horizon_sec,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "max_hold_sec": max_hold_sec,
            "fee_bps": fee_bps,
            "slip_bps": slip_bps,
            "funding_bps_per_hour": funding_bps_per_hour,
            "dedup_sec": dedup_sec,
            "offset_days": offset_days,
            "round_trip_cost_bps": round(round_trip_cost * 1e4, 4),
        },
        "n_signals": len(signals),
        "n_traded": len(trades),
        "n_skipped_no_kline": skipped,
        "n_anomalies": n_anomalies,
        "fixed": _aggregate(trades, "ret_fixed"),
        "triple_barrier": _aggregate(trades, "ret_tb"),
        "fixed_permutation_p": _permutation_p(trades, "ret_fixed", n_perm),
        "triple_permutation_p": _permutation_p(trades, "ret_tb", n_perm),
        "buckets_fixed": _bucket_stats(trades, "ret_fixed"),
        "buckets_triple": _bucket_stats(trades, "ret_tb"),
        "parity_score": None,
    }
    return report


def _save(report: Dict[str, Any]) -> Path:
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity
    from backend.services.scalp.scalp_validation_gate import evaluate_gate

    gate = evaluate_gate({
        "n": report.get("triple_barrier", {}).get("n", 0),
        "avg_net_ret": report.get("triple_barrier", {}).get("avg_net_ret", 0.0),
        "permutation_p": report.get("triple_permutation_p", 1.0),
        "buckets": report.get("buckets_triple", []),
        "parity_score": report.get("parity_score"),
    })
    report["gate"] = gate

    out_dir = _ROOT / "reports" / "scalp_backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (
        "scalp_backtest_%s_%s_d%d_o%d.json"
        % (
            _now_utc().strftime("%Y-%m-%d"),
            report.get("scenario", "run"),
            report.get("config", {}).get("days", 30),
            report.get("config", {}).get("offset_days", 0),
        )
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with system_identity():
        with SessionLocal() as db:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS scalp_backtest_run ("
                " id BIGSERIAL PRIMARY KEY,"
                " run_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                " scenario VARCHAR(32),"
                " config JSONB,"
                " metrics JSONB,"
                " gate JSONB)"
            ))
            db.execute(
                text(
                    "INSERT INTO scalp_backtest_run (scenario, config, metrics, gate) "
                    "VALUES (:s, :c, :m, :g)"
                ),
                {
                    "s": report.get("scenario"),
                    "c": json.dumps(report.get("config", {}), ensure_ascii=False),
                    "m": json.dumps({
                        "n_traded": report.get("n_traded"),
                        "fixed": report.get("fixed"),
                        "triple_barrier": report.get("triple_barrier"),
                        "fixed_permutation_p": report.get("fixed_permutation_p"),
                        "triple_permutation_p": report.get("triple_permutation_p"),
                    }, ensure_ascii=False),
                    "g": json.dumps(gate, ensure_ascii=False),
                },
            )
            db.commit()
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="scalp 真实信号回放回测")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--horizon-sec", type=int, default=1800)
    ap.add_argument("--sl-pct", type=float, default=0.005)
    ap.add_argument("--tp-pct", type=float, default=0.01)
    ap.add_argument("--max-hold-sec", type=int, default=7200)
    ap.add_argument("--fee-bps", type=float, default=None)
    ap.add_argument("--slip-bps", type=float, default=None)
    ap.add_argument("--funding-bps-per-hour", type=float, default=0.0)
    ap.add_argument("--dedup-sec", type=int, default=1800)
    ap.add_argument("--permutations", type=int, default=200)
    ap.add_argument("--scenario", choices=list(SCENARIOS.keys()), default="realistic")
    ap.add_argument("--exchange", default=None, help="K线交易所，默认取 SCALP_KLINE_EXCHANGE")
    ap.add_argument("--offset-days", type=int, default=0, help="信号窗口向历史偏移 N 天")
    args = ap.parse_args()

    cfg = SCENARIOS[args.scenario]
    report = run_scalp_backtest(
        days=args.days,
        horizon_sec=args.horizon_sec,
        sl_pct=args.sl_pct,
        tp_pct=args.tp_pct,
        max_hold_sec=args.max_hold_sec,
        fee_bps=args.fee_bps if args.fee_bps is not None else cfg["fee_bps"],
        slip_bps=args.slip_bps if args.slip_bps is not None else cfg["slip_bps"],
        funding_bps_per_hour=args.funding_bps_per_hour,
        dedup_sec=args.dedup_sec,
        n_perm=args.permutations,
        scenario=args.scenario,
        exchange=args.exchange,
        offset_days=args.offset_days,
    )
    if "error" in report:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    path = _save(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\n报告已保存:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
