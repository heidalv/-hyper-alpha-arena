# -*- coding: utf-8 -*-
"""风险几何实验（Y16）：止损宽度 × 浮盈锁，总 USD 口径、双段切分。

发现（Y13/Y15）：mid 层典型峰值 +1.2%，而 SL/失效位在 -5~-6% —— 赚小亏大的几何。
本脚本沿真实路径（尊重真实部分平仓）测试：
  SL ∈ {1.5, 2, 3, 4, 6}%  ×  锁 ∈ {无, 0.5/0.15, 1.0/0.3}
报总 USD / 均值 / 胜率 / ≤-2% / 前后段，找风险几何的稳健点。
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


def load_trades(days=30, tier="mid"):
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
    for tier in ("mid", "long"):
        poss, evs = load_trades(30, tier)
        if not poss:
            continue
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
            recs.append({
                "symbol": p["symbol"], "side": str(p["side"]), "entry": entry, "close": close,
                "sz0": sz0, "notional0": sz0 * entry,
                "base_usd": (float(p["unrealized_pnl"] or 0)
                             + float(p["partial_realized_pnl"] or 0)
                             - float(p["partial_fee_paid"] or 0)),
                "s": s, "i": i,
                "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
                "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                                  float(e["price"])) for e in by_pos.get(p["id"], [])],
                                key=lambda x: x[0]),
                "opened": str(p["opened_at"])[:19],
            })

        def sim(r, sl_pct, act, gap):
            sign = 1.0 if r["side"] == "long" else -1.0
            s, i0 = r["s"], r["i"]
            parts = r["parts"]
            pi = 0
            realized, fees, qty = 0.0, 0.0, r["sz0"]
            cur_sl = r["entry"] * (1 - sign * sl_pct / 100.0)
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
                if act and peak >= act:
                    cand = r["entry"] * (1 + sign * (peak - gap) / 100.0)
                    if (r["side"] == "long" and cand > cur_sl) or (r["side"] == "short" and cand < cur_sl):
                        cur_sl = cand
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

        med = sorted(r["opened"] for r in recs)[len(recs) // 2]
        print(f"\n===== tier={tier} n={len(recs)} 中位时间={med} =====")
        print(f"{'SL/锁':<16}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'≤-2%':>7}"
              f"{'前段USD':>10}{'后段USD':>10}")
        for sl_pct in (1.5, 2.0, 3.0, 4.0, 6.0):
            for act, gap in ((None, None), (0.5, 0.15), (1.0, 0.3)):
                usds = [sim(r, sl_pct, act, gap) for r in recs]
                pcts = [u / r["notional0"] * 100 for u, r in zip(usds, recs)]
                early = [u for u, r in zip(usds, recs) if r["opened"] < med]
                late = [u for u, r in zip(usds, recs) if r["opened"] >= med]
                lab = f"SL{sl_pct}%" + ("" if act is None else f"+锁{act}/{gap}")
                print(f"{lab:<16}{sum(usds):>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
                      f"{sum(1 for x in pcts if x > 0)/len(pcts):>7.3f}"
                      f"{sum(1 for x in pcts if x <= -2):>7}"
                      f"{sum(early):>+10.2f}{sum(late):>+10.2f}")
        print(f"{'实际基线':<16}{sum(r['base_usd'] for r in recs):>+10.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
