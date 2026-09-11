# -*- coding: utf-8 -*-
"""时间止损（死钱仓位）验证（Z6）：按「峰值相对 R」的时限退出。

思路：long 层 5–7 笔模式交易合计 -$88~90（单笔最大亏损源），其峰值均 <0.35R
（ASTER +1.11%/R6.68=0.17R、UNI +2.20%/6.5=0.34R、SOL +0.82%/6.67=0.12R）——
即"从未动过"的死钱。价格型保护会砍赢家（§30.3b），但**时限型**理论上不会：
赢家在 N 小时内就超过 kR 了。

测试：hold ≥ N 小时且峰值 < kR → 市价平仓（其余路径不变，尊重真实部分平仓）。
网格：k ∈ {0.2,0.3,0.5} × N ∈ {12,24,48}，long 层与 mid 层分别报总 USD / 逐月。
"""
from __future__ import annotations

import os
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


def load(tier, days=75):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price, sl_price,
                   size, original_size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier='{tier}' and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
        evs = [dict(r._mapping) for r in c.execute(text("""
            select position_id, quantity, price, created_at
            from position_exit_events
            where event_type='partial_exit_event' and created_at >= now() - interval '90 days'
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
    for tier in ("long", "mid"):
        poss, evs = load(tier)
        if not poss:
            continue
        by_pos = defaultdict(list)
        for e in evs:
            if e["quantity"] and e["price"]:
                by_pos[e["position_id"]].append(e)
        h1 = load_klines({p["symbol"] for p in poss})
        recs = []
        for p in poss:
            sym = p["symbol"]
            s = pick(h1, sym, int(p["opened_at"].timestamp()))
            if not s:
                continue
            entry = float(p["entry_price"] or 0)
            close = float(p["close_price"] or 0)
            sz0 = float(p["original_size"] or p["size"] or 0)
            if entry <= 0 or close <= 0 or sz0 <= 0:
                continue
            ts = int(p["opened_at"].timestamp())
            i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
            if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
                continue
            sl = float(p["sl_price"] or 0)
            r_pct = abs(entry - sl) / entry * 100 if sl > 0 else 6.0
            r_pct = min(max(r_pct, 0.5), 15.0)
            recs.append({
                "symbol": sym, "side": str(p["side"]), "entry": entry, "close": close,
                "sz0": sz0, "notional0": sz0 * entry,
                "base_usd": (float(p["unrealized_pnl"] or 0)
                             + float(p["partial_realized_pnl"] or 0)
                             - float(p["partial_fee_paid"] or 0)),
                "s": s, "i": i, "r_pct": r_pct,
                "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
                "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                                  float(e["price"])) for e in by_pos.get(p["id"], [])],
                                key=lambda x: x[0]),
                "mon": str(p["opened_at"])[:7], "reason": str(p["close_reason"] or "")[:22],
            })

        def sim(r, k, hours):
            if k is None:
                return r["base_usd"]
            sign = 1.0 if r["side"] == "long" else -1.0
            s, i0 = r["s"], r["i"]
            parts, pi = r["parts"], 0
            realized, fees, qty = 0.0, 0.0, r["sz0"]
            peak = 0.0
            for kk in range(i0, len(s)):
                ts, _o, h, l, c = s[kk]
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
                peak = max(peak, hi)
                hold_h = (ts - s[i0][0]) / 3600.0
                if hold_h >= hours and peak < k * r["r_pct"]:
                    realized += qty * sign * (c - r["entry"])
                    fees += qty * c * (FEE_SIDE + SLIP) * 2
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

        print(f"\n===== tier={tier} n={len(recs)}（近 75 天）=====")
        print(f"{'方案':<20}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'≤-2%':>7}  逐月USD")
        for label, k, hours in ([("实际", None, None)]
                                + [(f"峰值<{k}R @ {h}h", k, h)
                                   for k in (0.2, 0.3, 0.5) for h in (12, 24, 48)]):
            usds = [sim(r, k, hours) for r in recs]
            pcts = [u / r["notional0"] * 100 for u, r in zip(usds, recs)]
            bym = defaultdict(float)
            for u, r in zip(usds, recs):
                bym[r["mon"]] += u
            bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
            print(f"{label:<20}{sum(usds):>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
                  f"{sum(1 for x in pcts if x > 0)/len(pcts):>7.3f}"
                  f"{sum(1 for x in pcts if x <= -2):>7}  {bym_s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
