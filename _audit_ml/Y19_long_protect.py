# -*- coding: utf-8 -*-
"""long 层「不伤右尾」保护设计（Y19）：条件化保本/锁利。

问题：long 层 5 笔「浮盈→大亏」-$87.96（单笔 -$17.59），峰值 0.7~2.4%；
但统一加锁会伤右尾（LINK 峰值 +6.98% → 锁在 +0.36%），导致总 USD 从 +$55.41 掉到 +$39.77。

假设：**峰值相对风险 R 很小**（peak/R < 0.5）的仓位才是"失败仓"，应在保本位砍掉；
峰值已超过 0.5R 的仓位保留趋势跟随空间。
测试变体（沿真实路径、尊重真实部分平仓、总 USD）：
  BASE  : 现状（Chandelier + 2%/3% 锁）
  V1    : peak≥0.5% → SL 推保本
  V2    : peak≥0.5% 且 peak<1.5% → 保本；peak≥1.5% 走现状
  V3    : peak≥0.5% → SL 推 peak×0.5
  V4    : peak<0.5R 且 peak≥0.5% → 保本（R = |entry-sl|/entry）
  V5    : V4 + 持仓 ≥4h 才启用
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
SLIP = 0.0005


def load(days=45):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price, sl_price,
                   size, original_size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier='long' and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
        evs = [dict(r._mapping) for r in c.execute(text("""
            select position_id, quantity, price, created_at
            from position_exit_events
            where event_type='partial_exit_event' and created_at >= now() - interval '55 days'
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
    poss, evs = load(45)
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
        sl = float(p["sl_price"] or 0)
        r_pct = abs(entry - sl) / entry * 100 if sl > 0 else 6.0
        r_pct = min(max(r_pct, 0.5), 15.0)
        recs.append({
            "symbol": p["symbol"], "side": str(p["side"]), "entry": entry, "close": close,
            "sz0": sz0, "notional0": sz0 * entry, "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "s": s, "i": i, "r_pct": r_pct, "sl_pct": r_pct,
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                              float(e["price"])) for e in by_pos.get(p["id"], [])],
                            key=lambda x: x[0]),
        })

    def sim(r, variant):
        if variant == "BASE":
            return r["base_usd"]
        sign = 1.0 if r["side"] == "long" else -1.0
        s, i0 = r["s"], r["i"]
        parts, pi = r["parts"], 0
        realized, fees, qty = 0.0, 0.0, r["sz0"]
        cur_sl = r["entry"] * (1 - sign * r["sl_pct"] / 100.0)
        peak = 0.0
        for k in range(i0, len(s)):
            ts, _o, h, l, c = s[k]
            while pi < len(parts) and parts[pi][0] <= ts:
                _t, q, px = parts[pi]
                q = min(q, qty)
                realized += q * sign * (px - r["entry"])
                fees += q * px * (FEE_SIDE + SLIP) * 2
                qty -= q
                pi += 1
            if qty <= 1e-12:
                break
            hi = sign * (h - r["entry"]) / r["entry"] * 100
            lo = sign * (l - r["entry"]) / r["entry"] * 100
            peak = max(peak, hi)
            hold_h = (ts - s[i0][0]) / 3600.0
            cand = None
            if variant == "V1" and peak >= 0.5:
                cand = 0.0
            elif variant == "V2" and 0.5 <= peak < 1.5:
                cand = 0.0
            elif variant == "V3" and peak >= 0.5:
                cand = peak * 0.5
            elif variant == "V4" and peak >= 0.5 and peak < 0.5 * r["r_pct"]:
                cand = 0.0
            elif variant == "V5" and peak >= 0.5 and peak < 0.5 * r["r_pct"] and hold_h >= 4.0:
                cand = 0.0
            if cand is not None:
                new_sl = r["entry"] * (1 + sign * cand / 100.0)
                if (r["side"] == "long" and new_sl > cur_sl) or (r["side"] == "short" and new_sl < cur_sl):
                    cur_sl = new_sl
            if lo <= sign * (cur_sl - r["entry"]) / r["entry"] * 100:
                px = cur_sl
                realized += qty * sign * (px - r["entry"])
                fees += qty * px * (FEE_SIDE + SLIP) * 2
                qty = 0.0
                break
            if ts >= r["close_ts"]:
                realized += qty * sign * (r["close"] - r["entry"])
                fees += qty * r["close"] * (FEE_SIDE + SLIP) * 2
                qty = 0.0
                break
        if qty > 1e-12:
            realized += qty * sign * (s[-1][4] - r["entry"])
            fees += qty * s[-1][4] * (FEE_SIDE + SLIP) * 2
        return realized - fees

    print(f"long 层 n={len(recs)}（近 45 天）")
    print(f"{'变体':<8}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'≤-2%':>7}{'最大单笔':>10}")
    for v in ("BASE", "V1", "V2", "V3", "V4", "V5"):
        usds = [sim(r, v) for r in recs]
        pcts = [u / r["notional0"] * 100 for u, r in zip(usds, recs)]
        print(f"{v:<8}{sum(usds):>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
              f"{sum(1 for x in pcts if x > 0)/len(pcts):>7.3f}"
              f"{sum(1 for x in pcts if x <= -2):>7}{min(usds):>+10.2f}")

    print("\n=== 逐笔（BASE vs V2 vs V4）===")
    for r in recs:
        b, v2, v4 = sim(r, "BASE"), sim(r, "V2"), sim(r, "V4")
        if abs(b) > 5 or b != v2 or b != v4:
            print(f"  {r['symbol']:<8} 峰值={r['peak']:>+6.2f}% R={r['r_pct']:>5.2f}% "
                  f"BASE={b:>+8.2f} V2={v2:>+8.2f} V4={v4:>+8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
