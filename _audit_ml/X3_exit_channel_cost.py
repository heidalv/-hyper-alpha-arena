# -*- coding: utf-8 -*-
"""主动出场通道损失归因（X3）：机械策略 vs 实际，逐 close_reason。

X2 发现：近 14 天 70 笔的机械出场反事实 +0.515%/笔（胜率 0.686），
而实际 -0.267%/笔（胜率 0.457）——差 -0.78%/笔。本脚本逐通道拆解：
  - 每个 close_reason 的 n / 实际净 / 机械 C0 反事实 / 机械 L4 反事实 / 峰值均值；
  - 「主动出场代价」= 机械反事实 - 实际（正 = 该通道平仓比机械管理差）；
  - 平仓时 ROI 分布（有多少笔在亏损状态下被主动平掉、峰值却为正）。
输出：`data/exit_channel_cost.json`
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
OUT = ROOT / "data" / "exit_channel_cost.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load_trades(days=30):
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


def sim_ladder(s, i, entry, side, *, sl_pct=6.0, stages=(), max_h=168):
    sign = 1.0 if side == "long" else -1.0
    cur_sl = entry * (1 - sign * sl_pct / 100.0)
    peak = 0.0
    for k in range(i, min(i + int(max_h) + 1, len(s))):
        _ts, _o, h, l, c = s[k]
        hold = k - i
        hi_roi = sign * (h - entry) / entry * 100
        lo_roi = sign * (l - entry) / entry * 100
        peak = max(peak, hi_roi)
        if lo_roi <= sign * (cur_sl - entry) / entry * 100:
            roi = sign * (cur_sl - entry) / entry * 100
            return roi - cost_pct(hold), hold, "sl", peak
        for act, cb in stages:
            if peak >= act:
                lock = peak - cb if cb > 0 else 0.15
                new_sl = entry * (1 + sign * lock / 100.0)
                if (side == "long" and new_sl > cur_sl) or (side == "short" and new_sl < cur_sl):
                    cur_sl = new_sl
        if k == min(i + int(max_h), len(s) - 1):
            roi = sign * (c - entry) / entry * 100
            return roi - cost_pct(hold), hold, "timeout", peak
    return None, 0, "no_data", peak


def fam(reason: str) -> str:
    r = str(reason or "?")
    if r.startswith("exit_policy:"):
        return r
    if r.startswith("long_trend_v2"):
        return "long_trend_v2"
    if "no_progress" in r:
        return "no_progress"
    if r.startswith("trend_broken"):
        return "trend_broken"
    if r.startswith("profit_drawdown"):
        return "profit_drawdown"
    if r.startswith("master_running"):
        return "master_running"
    if r.startswith("breakeven"):
        return "breakeven_tp"
    if r.startswith("thesis_invalidation") or "invalidation" in r:
        return "thesis_invalidation"
    if r.startswith("sl"):
        return "sl"
    if r.startswith("tp"):
        return "tp"
    return r[:22]


def main() -> int:
    trades = load_trades(30)
    h1 = load_klines({t["symbol"] for t in trades})
    print(f"近 30 天 mid/long 已平仓: {len(trades)} 笔")

    recs = []
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
        c0 = sim_ladder(s, i, entry, side, stages=[(3.0, 1.5)])
        l4 = sim_ladder(s, i, entry, side, stages=[(1.5, 1.0)])
        recs.append({
            "fam": fam(t["close_reason"]), "symbol": t["symbol"], "side": side,
            "peak": round(float(t["peak_pnl_pct"] or 0) * 100, 3),
            "actual": round(raw - cost_pct(hold_h), 3),
            "c0": round(c0[0], 3) if c0[0] is not None else None,
            "l4": round(l4[0], 3) if l4[0] is not None else None,
            "hold_h": round(hold_h, 2),
        })

    def agg(rows_, label):
        if not rows_:
            return
        n = len(rows_)
        a = sum(x["actual"] for x in rows_) / n
        c0 = [x["c0"] for x in rows_ if x["c0"] is not None]
        l4 = [x["l4"] for x in rows_ if x["l4"] is not None]
        c0m = sum(c0) / len(c0) if c0 else 0
        l4m = sum(l4) / len(l4) if l4 else 0
        print(f"  {label:<26} n={n:>3} 实际={a:>+7.2f}% 机械C0={c0m:>+7.2f}% 机械L4={l4m:>+7.2f}% "
              f"主动代价={c0m - a:>+7.2f}% 峰值均值={sum(x['peak'] for x in rows_)/n:>+6.2f}%")

    print("\n=== 逐通道（n≥3）===")
    byfam = defaultdict(list)
    for x in recs:
        byfam[x["fam"]].append(x)
    for k in sorted(byfam, key=lambda kk: -len(byfam[kk])):
        if len(byfam[k]) >= 3:
            agg(byfam[k], k)

    print("\n=== 按「平仓时是否亏损」× 「峰值是否为正」===")
    agg([x for x in recs if x["actual"] < 0 and x["peak"] >= 0.5], "亏损平仓且曾浮盈≥0.5%")
    agg([x for x in recs if x["actual"] < 0 and x["peak"] < 0.5], "亏损平仓且峰值<0.5%")
    agg([x for x in recs if x["actual"] >= 0], "盈利平仓")

    print("\n=== 总体 ===")
    agg(recs, "全部")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": len(recs), "recs": recs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
