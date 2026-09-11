# -*- coding: utf-8 -*-
"""浮盈保护阶梯网格扫描（X4）：14 天 / 30 天双窗口。

X2/X3 结论：机械出场≈实际，但「曾浮盈≥0.5% 后亏损离场」44 笔里机械 L4 更好
（-1.03% vs C0 -1.36%）。本脚本在双窗口上扫描保护阶梯，找稳健设置：
  激活阈值 × 回撤幅度，单段/多段累积（每段取最紧的锁定价）。

输出：`data/ladder_grid.json`
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
OUT = ROOT / "data" / "ladder_grid.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load_trades(days):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        return [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price,
                   peak_pnl_pct, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]


def load_klines(symbols):
    h1 = defaultdict(list)
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                h1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return h1


def pick(series, sym):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 200:
            return v
    return None


def sim(s, i, entry, side, stages, sl_pct=6.0, max_h=168):
    sign = 1.0 if side == "long" else -1.0
    cur_sl = entry * (1 - sign * sl_pct / 100.0)
    peak = 0.0
    for k in range(i, min(i + int(max_h) + 1, len(s))):
        _ts, _o, h, l, c = s[k]
        hold = k - i
        hi = sign * (h - entry) / entry * 100
        lo = sign * (l - entry) / entry * 100
        peak = max(peak, hi)
        if lo <= sign * (cur_sl - entry) / entry * 100:
            return sign * (cur_sl - entry) / entry * 100 - cost_pct(hold), hold
        for act, cb in stages:
            if peak >= act:
                new_sl = entry * (1 + sign * (peak - cb) / 100.0)
                if (side == "long" and new_sl > cur_sl) or (side == "short" and new_sl < cur_sl):
                    cur_sl = new_sl
        if k == min(i + int(max_h), len(s) - 1):
            return sign * (c - entry) / entry * 100 - cost_pct(hold), hold
    return None, 0


GRID = {
    "A 现行[(3.0,1.5)]": [(3.0, 1.5)],
    "B [(1.5,1.0)]": [(1.5, 1.0)],
    "C [(1.5,1.0),(3.0,1.5)]": [(1.5, 1.0), (3.0, 1.5)],
    "D [(0.75,0.5),(1.5,1.0),(3.0,1.5)]": [(0.75, 0.5), (1.5, 1.0), (3.0, 1.5)],
    "E [(1.0,0.5),(2.0,1.0),(3.0,1.5)]": [(1.0, 0.5), (2.0, 1.0), (3.0, 1.5)],
    "F [(0.75,0.5)]": [(0.75, 0.5)],
    "G [(1.0,0.75),(2.5,1.25)]": [(1.0, 0.75), (2.5, 1.25)],
    "H [(1.5,0.75),(3.0,1.5)]": [(1.5, 0.75), (3.0, 1.5)],
    "I [(1.0,0.6),(2.0,1.0)]": [(1.0, 0.6), (2.0, 1.0)],
}


def run(days):
    trades = load_trades(days)
    h1 = load_klines({t["symbol"] for t in trades})
    rows = []
    for t in trades:
        s = pick(h1, t["symbol"])
        if not s:
            continue
        entry = float(t["entry_price"] or 0)
        close = float(t["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        ts = int(t["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None:
            continue
        side = str(t["side"] or "long")
        hold_h = (t["closed_at"] - t["opened_at"]).total_seconds() / 3600 if t["closed_at"] else 0
        raw = (close - entry) / entry * 100 if side == "long" else (entry - close) / entry * 100
        rows.append({"s": s, "i": i, "entry": entry, "side": side,
                     "peak": float(t["peak_pnl_pct"] or 0) * 100,
                     "actual": raw - cost_pct(hold_h)})
    return rows


def main() -> int:
    out = {}
    for days in (14, 30):
        rows = run(days)
        print(f"\n=== {days} 天窗口：n={len(rows)} ===")
        print(f"{'阶梯':<40}{'净均值':>9}{'中位':>8}{'胜率':>7}{'合计':>9}{'峰值≥0.5亏损组':>16}")
        res = {}
        # 「曾浮盈≥0.5% 且最终亏损」子集用现行 C0 的实际结果界定
        lowsub = [r for r in rows if r["peak"] >= 0.5 and r["actual"] < 0]
        for name, stages in GRID.items():
            nets, subnets = [], []
            for r in rows:
                v = sim(r["s"], r["i"], r["entry"], r["side"], stages)
                if v[0] is None:
                    continue
                nets.append(v[0])
                if r in lowsub:
                    subnets.append(v[0])
            mean = sum(nets) / len(nets)
            res[name] = {"n": len(nets), "mean": round(mean, 3),
                         "median": round(st.median(nets), 3),
                         "win": round(sum(1 for x in nets if x > 0) / len(nets), 3),
                         "sum": round(sum(nets), 1),
                         "loss_peak_subset_mean": (round(sum(subnets) / len(subnets), 3)
                                                   if subnets else None)}
            sub = res[name]["loss_peak_subset_mean"]
            print(f"{name:<40}{res[name]['mean']:>+9.3f}{res[name]['median']:>+8.3f}"
                  f"{res[name]['win']:>7.3f}{res[name]['sum']:>+9.1f}"
                  f"{(f'{sub:+.3f}(n{len(subnets)})' if sub is not None else '-'):>16}")
        out[f"{days}d"] = res
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(), "grid": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
