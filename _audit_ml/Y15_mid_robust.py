# -*- coding: utf-8 -*-
"""mid 层浮盈锁稳健性（Y15）：时间切分 / 滑点 / 逐币 / 与入场门叠加。

结论候选：mid 层 EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT 3.0→0.5、
CALLBACK 1.5→0.15（固定锁 peak-0.15）。本脚本验证：
  1. 前段/后段时间切分；
  2. 滑点敏感度（单边 5/15/30bp）；
  3. 逐币；
  4. 与 learned 门（round-17/18）叠加后的效果。
"""
from __future__ import annotations

import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

FEE_SIDE = 0.0005


def load_trades(days=30):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price, sl_price,
                   size, original_size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier='mid' and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
        evs = [dict(r._mapping) for r in c.execute(text("""
            select position_id, quantity, price, created_at
            from position_exit_events
            where event_type='partial_exit_event' and created_at >= now() - interval '40 days'
            order by position_id, created_at
        """)).fetchall()]
    return poss, evs


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


def pick(series, sym, ts):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 200 and v[0][0] <= ts <= v[-1][0] + 86400:
            return v
    return None


def main() -> int:
    poss, evs = load_trades(30)
    by_pos = defaultdict(list)
    for e in evs:
        if e["quantity"] and e["price"]:
            by_pos[e["position_id"]].append(e)
    h1 = load_klines({p["symbol"] for p in poss})

    recs = []
    for p in poss:
        entry = float(p["entry_price"] or 0)
        close = float(p["close_price"] or 0)
        sz0 = float(p["original_size"] or p["size"] or 0)
        if entry <= 0 or close <= 0 or sz0 <= 0:
            continue
        ts = int(p["opened_at"].timestamp())
        s = pick(h1, p["symbol"], ts)
        if not s:
            continue
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
            continue
        notional0 = sz0 * entry
        total_usd = (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                     - float(p["partial_fee_paid"] or 0))
        sl = float(p["sl_price"] or 0)
        sl_pct = abs(entry - sl) / entry * 100 if sl > 0 else 6.0
        sl_pct = min(max(sl_pct, 0.5), 12.0)
        recs.append({
            "symbol": p["symbol"], "side": str(p["side"]), "entry": entry, "close": close,
            "sz0": sz0, "notional0": notional0, "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "base_usd": total_usd, "s": s, "i": i, "sl_pct": sl_pct,
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                              float(e["price"])) for e in by_pos.get(p["id"], [])],
                            key=lambda x: x[0]),
            "opened": str(p["opened_at"])[:19],
        })

    def sim(r, act, gap, slip):
        if act is None:
            return r["base_usd"]
        sign = 1.0 if r["side"] == "long" else -1.0
        s, i0 = r["s"], r["i"]
        parts = r["parts"]
        pi = 0
        realized, fees, qty = 0.0, 0.0, r["sz0"]
        cur_sl = r["entry"] * (1 - sign * r["sl_pct"] / 100.0)
        peak = 0.0
        for k in range(i0, len(s)):
            ts, _o, h, l, c = s[k]
            while pi < len(parts) and parts[pi][0] <= ts:
                _t, q, px = parts[pi]
                q = min(q, qty)
                realized += q * sign * (px - r["entry"])
                fees += q * px * (FEE_SIDE + slip) * 2
                qty -= q
                pi += 1
            if qty <= 1e-12:
                break
            hi = sign * (h - r["entry"]) / r["entry"] * 100
            lo = sign * (l - r["entry"]) / r["entry"] * 100
            peak = max(peak, hi)
            if peak >= act:
                cand = r["entry"] * (1 + sign * (peak - gap) / 100.0)
                if (r["side"] == "long" and cand > cur_sl) or (r["side"] == "short" and cand < cur_sl):
                    cur_sl = cand
            if lo <= sign * (cur_sl - r["entry"]) / r["entry"] * 100:
                px = cur_sl
                realized += qty * sign * (px - r["entry"])
                fees += qty * px * (FEE_SIDE + slip) * 2
                qty = 0.0
                break
            if ts >= r["close_ts"]:
                realized += qty * sign * (r["close"] - r["entry"])
                fees += qty * r["close"] * (FEE_SIDE + slip) * 2
                qty = 0.0
                break
        if qty > 1e-12:
            realized += qty * sign * (s[-1][4] - r["entry"])
            fees += qty * s[-1][4] * (FEE_SIDE + slip) * 2
        return realized - fees

    med = sorted(r["opened"] for r in recs)[len(recs) // 2]
    print(f"mid 层 n={len(recs)} 中位时间={med}")

    def report(label, sub, act, gap, slip=0.0005):
        usds = [sim(r, act, gap, slip) for r in sub]
        pcts = [u / r["notional0"] * 100 for u, r in zip(usds, sub)]
        return (label, len(sub), round(sum(pcts) / len(pcts), 3),
                round(st.median(pcts), 3), round(sum(1 for x in pcts if x > 0) / len(pcts), 3),
                round(sum(usds), 2), sum(1 for x in pcts if x <= -2))

    print(f"\n{'方案':<26}{'n':>4}{'均值%':>9}{'中位%':>8}{'胜率':>7}{'总USD':>10}{'≤-2%':>7}")
    for act, gap in ((None, None), (0.5, 0.15), (0.4, 0.15), (0.6, 0.15)):
        lab = "基线" if act is None else f"锁 {act}/{gap}"
        for seg_name, seg in (("全样本", recs),
                              ("前段", [r for r in recs if r["opened"] < med]),
                              ("后段", [r for r in recs if r["opened"] >= med])):
            r_ = report(seg_name, seg, act, gap)
            print(f"{lab + '/' + seg_name:<26}{r_[1]:>4}{r_[2]:>+9.3f}{r_[3]:>+8.3f}"
                  f"{r_[4]:>7.3f}{r_[5]:>+10.2f}{r_[6]:>7}")

    print(f"\n=== 滑点敏感度（锁 0.5/0.15）===")
    print(f"{'单边滑点':<12}{'均值%':>9}{'总USD':>10}{'≤-2%':>7} | {'基线总USD':>10}")
    for slip in (0.0005, 0.0015, 0.003):
        usds = [sim(r, 0.5, 0.15, slip) for r in recs]
        pcts = [u / r["notional0"] * 100 for u, r in zip(usds, recs)]
        base = sum(r["base_usd"] for r in recs)
        print(f"{slip*10000:>8.0f}bp{sum(pcts)/len(pcts):>+9.3f}{sum(usds):>+10.2f}"
              f"{sum(1 for x in pcts if x <= -2):>7} | {base:>+10.2f}")

    print(f"\n=== 逐币（锁 0.5/0.15 vs 基线，总USD）===")
    bysym = defaultdict(list)
    for r in recs:
        bysym[r["symbol"]].append(r)
    for sym in sorted(bysym, key=lambda k: -len(bysym[k])):
        v = bysym[sym]
        if len(v) < 4:
            continue
        b = sum(r["base_usd"] for r in v)
        l_ = sum(sim(r, 0.5, 0.15, 0.0005) for r in v)
        print(f"  {sym:<10} n={len(v):>3} 基线={b:>+8.2f} 加锁={l_:>+8.2f} Δ={l_ - b:>+8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
