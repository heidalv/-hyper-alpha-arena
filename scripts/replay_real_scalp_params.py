"""真实 scalp 持仓参数反事实回放（BTC/ETH/SOL）。

输入：paper_positions 里 60 天内已平仓的真实 scalp 单（实际进场价/方向/开仓时间）。
方法：在真实 asterdex 5m K 线上，用不同 SL/TP/最大持仓配置重放退出：
  - actual        ：持仓里实际存的 SL/TP，最大持仓 3h
  - target_12_18  ：SL 1.2% / TP 1.8% / 20min（系统目标参数）
  - target_12_30  ：SL 1.2% / TP 3.0% / 20min
  - wide_30_30    ：SL 3.0% / TP 3.0% / 3h
成本：taker fee 0.5bps + 滑点 2.97bps（往返 6.94bps）。

输出：reports/scalp_real_params/kline_replay_real_YYYY-MM-DD.json
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]

SYMBOLS = ["BTC", "ETH", "SOL"]
EXCHANGE = "asterdex"
ROUND_TRIP_COST = (0.5 * 2 + 2.97 * 2) / 1e4

CONFIGS = {
    "actual": {"sl_pct": None, "tp_pct": None, "max_hold_min": 180},
    "target_12_18": {"sl_pct": 0.012, "tp_pct": 0.018, "max_hold_min": 20},
    "target_12_30": {"sl_pct": 0.012, "tp_pct": 0.030, "max_hold_min": 20},
    "wide_30_30": {"sl_pct": 0.030, "tp_pct": 0.030, "max_hold_min": 180},
}


def _load_positions() -> List[Dict[str, Any]]:
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from sqlalchemy import text

    out = []
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT id, symbol, side, entry_price, tp_price, sl_price, "
                    "EXTRACT(EPOCH FROM opened_at)::bigint AS opened_ts "
                    "FROM paper_positions "
                    "WHERE trade_nature='scalp' AND status='closed' "
                    "AND symbol IN ('BTC','ETH','SOL') "
                    "AND opened_at >= now() - interval '60 days' "
                    "AND entry_price > 0 ORDER BY opened_at"
                )
            ).mappings().all()
    for r in rows:
        out.append({
            "pos_id": int(r["id"]),
            "symbol": str(r["symbol"]),
            "side": str(r["side"]),
            "entry": float(r["entry_price"]),
            "tp": float(r["tp_price"]) if r["tp_price"] else None,
            "sl": float(r["sl_price"]) if r["sl_price"] else None,
            "opened_ts": int(r["opened_ts"]),
        })
    return out


def _load_klines(symbols, start_ts, end_ts) -> Dict[str, Dict[str, list]]:
    from backend.core.tenant import system_identity
    from backend.database.connection import MarketSessionLocal
    from sqlalchemy import text

    ph = ",".join(":s%d" % i for i in range(len(symbols)))
    params = {"start": start_ts, "end": end_ts}
    params.update({"s%d" % i: s for i, s in enumerate(symbols)})
    by_sym: Dict[str, Dict[int, tuple]] = {}
    with system_identity():
        with MarketSessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT symbol, timestamp, open_price, high_price, low_price, close_price "
                    "FROM crypto_klines WHERE period='5m' AND exchange='asterdex' "
                    "AND symbol IN (%s) AND timestamp >= :start AND timestamp <= :end "
                    "ORDER BY symbol, timestamp" % ph
                ),
                params,
            ).mappings().all()
    for r in rows:
        sym = str(r["symbol"])
        ts = int(r["timestamp"])
        try:
            by_sym.setdefault(sym, {})[ts] = (
                float(r["open_price"]), float(r["high_price"]),
                float(r["low_price"]), float(r["close_price"]),
            )
        except Exception:
            continue
    out = {}
    for sym, d in by_sym.items():
        ts_sorted = sorted(d.keys())
        out[sym] = {
            "ts": ts_sorted,
            "open": [d[t][0] for t in ts_sorted],
            "high": [d[t][1] for t in ts_sorted],
            "low": [d[t][2] for t in ts_sorted],
            "close": [d[t][3] for t in ts_sorted],
        }
    return out


def _simulate(entry, side, opened_ts, sl_pct, tp_pct, max_hold_min, k):
    if not k or not k["ts"]:
        return None
    import bisect
    start = bisect.bisect_right(k["ts"], opened_ts)
    if start >= len(k["ts"]):
        return None
    sl = entry * (1 - sl_pct) if side == "long" else entry * (1 + sl_pct)
    tp = entry * (1 + tp_pct) if side == "long" else entry * (1 - tp_pct)
    last_idx = min(start + max(1, int(max_hold_min / 5)), len(k["ts"]) - 1)
    exit_price = k["close"][last_idx]
    reason = "timeout"
    for j in range(start, last_idx + 1):
        hi = k["high"][j]
        lo = k["low"][j]
        if side == "long":
            if lo <= sl:
                exit_price = sl
                reason = "sl"
                break
            if hi >= tp:
                exit_price = tp
                reason = "tp"
                break
        else:
            if hi >= sl:
                exit_price = sl
                reason = "sl"
                break
            if lo <= tp:
                exit_price = tp
                reason = "tp"
                break
    direction = 1.0 if side == "long" else -1.0
    return direction * (exit_price / entry - 1.0) - ROUND_TRIP_COST


def _agg(rets: List[float]) -> Dict[str, Any]:
    if not rets:
        return {"n": 0, "win_rate": 0.0, "avg_net_ret": 0.0, "profit_factor": None}
    n = len(rets)
    wins = sum(1 for r in rets if r > 0)
    gw = sum(r for r in rets if r > 0)
    gl = sum(r for r in rets if r < 0)
    return {
        "n": n,
        "win_rate": round(wins / n, 4),
        "avg_net_ret": round(sum(rets) / n, 6),
        "profit_factor": round(gw / abs(gl), 4) if gl else None,
    }


def main() -> int:
    positions = _load_positions()
    if not positions:
        print("无真实持仓")
        return 1
    start_ts = min(p["opened_ts"] for p in positions) - 3600
    end_ts = max(p["opened_ts"] for p in positions) + 12 * 3600
    klines = _load_klines(SYMBOLS, start_ts, end_ts)

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exchange": EXCHANGE,
        "round_trip_cost_bps": round(ROUND_TRIP_COST * 1e4, 4),
        "configs": CONFIGS,
        "by_symbol_config": {},
        "total_by_config": {},
    }
    for cfg_name, cfg in CONFIGS.items():
        totals: List[float] = []
        sym_map: Dict[str, List[float]] = {s: [] for s in SYMBOLS}
        for p in positions:
            k = klines.get(p["symbol"])
            if cfg_name == "actual":
                sl_pct = None
                tp_pct = None
                if p["sl"] and p["sl"] > 0:
                    sl_pct = abs(p["entry"] - p["sl"]) / p["entry"]
                if p["tp"] and p["tp"] > 0:
                    tp_pct = abs(p["tp"] - p["entry"]) / p["entry"]
                if not sl_pct or not tp_pct:
                    continue
            else:
                sl_pct = cfg["sl_pct"]
                tp_pct = cfg["tp_pct"]
            ret = _simulate(
                p["entry"], p["side"], p["opened_ts"],
                sl_pct, tp_pct, cfg["max_hold_min"], k,
            )
            if ret is None:
                continue
            sym_map[p["symbol"]].append(ret)
            totals.append(ret)
        report["by_symbol_config"][cfg_name] = {
            s: _agg(v) for s, v in sym_map.items()
        }
        report["total_by_config"][cfg_name] = _agg(totals)
        print(
            "%s: n=%d win=%.1f%% avg=%.4f%% PF=%s"
            % (
                cfg_name,
                report["total_by_config"][cfg_name]["n"],
                report["total_by_config"][cfg_name]["win_rate"] * 100,
                report["total_by_config"][cfg_name]["avg_net_ret"] * 100,
                report["total_by_config"][cfg_name]["profit_factor"],
            )
        )

    out_dir = ROOT / "reports" / "scalp_real_params"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("replay_real_%s.json" % datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("报告已保存:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
