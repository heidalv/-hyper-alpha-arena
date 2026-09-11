# -*- coding: utf-8 -*-
"""门 × 锁 交互定标（Y24）：在 learned 门放行的子集上扫浮盈锁参数。

Y23 发现：门+锁（-$73.57）远差于仅门（-$8.62）——门选动量延续，紧锁杀延续。
本脚本在门放行子集（n=71 mid）上扫锁参数，并在全量上对照，找最优组合：
  锁激活 ∈ {0.5, 1.0, 1.5, 2.0, 3.0, 不锁}，回撤固定 0.15（或按激活×0.3）。
另测：门放行 + 仅「保本」保护（峰值≥act → SL 推保本，不追踪）。
"""
from __future__ import annotations

import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, load_klines, pick, learned_ok_prod  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
FEE_SIDE = 0.0005
SLIP = 0.0005


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, timeframe_tier, entry_price, close_price, sl_price,
                   size, original_size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier='mid' and status='closed'
              and closed_at >= now() - interval '30 days'
            order by opened_at
        """)).fetchall()]
        evs = [dict(r._mapping) for r in c.execute(text("""
            select position_id, quantity, price, created_at
            from position_exit_events
            where event_type='partial_exit_event' and created_at >= now() - interval '40 days'
            order by position_id, created_at
        """)).fetchall()]
    by_pos = defaultdict(list)
    for e in evs:
        if e["quantity"] and e["price"]:
            by_pos[e["position_id"]].append(e)
    h1, d1 = load_klines({p["symbol"] for p in poss})
    feats = {}
    for sym in {p["symbol"] for p in poss}:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if s is None or ds is None or len(s) < 300 or len(ds) < 70:
            continue
        feats[sym] = (s, build_bar_features(s, ds))

    recs = []
    for p in poss:
        sym = p["symbol"]
        if sym not in feats:
            continue
        s, (reg_arr, pos_arr, chg_arr) = feats[sym]
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
        sl_pct = abs(entry - sl) / entry * 100 if sl > 0 else 6.0
        sl_pct = min(max(sl_pct, 0.5), 12.0)
        recs.append({
            "symbol": sym, "side": str(p["side"]), "entry": entry, "close": close,
            "sz0": sz0, "notional0": sz0 * entry, "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "s": s, "i": i, "sl_pct": sl_pct,
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "parts": sorted([(int(e["created_at"].timestamp()), float(e["quantity"]),
                              float(e["price"])) for e in by_pos.get(p["id"], [])],
                            key=lambda x: x[0]),
            "allow": learned_ok_prod(reg_arr[i], pos_arr[i], chg_arr[i]),
            "opened": str(p["opened_at"])[:19],
        })

    def sim(r, act, gap, mode="trail"):
        if act is None:
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
            if peak >= act:
                if mode == "trail":
                    cand = peak - gap
                else:  # breakeven
                    cand = 0.0
                new_sl = r["entry"] * (1 + sign * cand / 100.0)
                if (r["side"] == "long" and new_sl > cur_sl) or (r["side"] == "short" and new_sl < cur_sl):
                    cur_sl = new_sl
                cur_roi = sign * (c - r["entry"]) / r["entry"] * 100
                if mode == "trail" and peak - cur_roi >= gap:
                    px = r["entry"] * (1 + sign * (peak - gap) / 100.0)
                    realized += qty * sign * (px - r["entry"])
                    fees += qty * px * (FEE_SIDE + SLIP) * 2
                    qty = 0.0
                    break
            if lo <= sign * (cur_sl - r["entry"]) / r["entry"] * 100:
                realized += qty * sign * (cur_sl - r["entry"])
                fees += qty * cur_sl * (FEE_SIDE + SLIP) * 2
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
    gated = [r for r in recs if r["allow"]]
    print(f"mid 全量 n={len(recs)}，门放行 n={len(gated)}，中位时间={med}")

    def report(label, rows, act, gap, mode="trail"):
        usds = [sim(r, act, gap, mode) for r in rows]
        pcts = [u / r["notional0"] * 100 for u, r in zip(usds, rows)]
        early = [u for u, r in zip(usds, rows) if r["opened"] < med]
        late = [u for u, r in zip(usds, rows) if r["opened"] >= med]
        return (f"{label:<26}{len(rows):>4}{sum(usds):>+10.2f}{sum(pcts)/len(pcts):>+9.3f}"
                f"{sum(1 for x in pcts if x > 0)/len(pcts):>7.3f}"
                f"{sum(1 for x in pcts if x <= -2):>7}{sum(early):>+10.2f}{sum(late):>+10.2f}")

    for label, rows in (("门放行子集", gated), ("全量", recs)):
        print(f"\n=== {label} n={len(rows)} ===")
        print(f"{'方案':<26}{'n':>4}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'≤-2%':>7}"
              f"{'前段USD':>10}{'后段USD':>10}")
        print(report("不锁", rows, None, None))
        for act in (0.5, 1.0, 1.5, 2.0, 3.0):
            gap = round(max(0.15, act * 0.3), 2)
            print(report(f"追踪锁 {act}/{gap}", rows, act, gap, "trail"))
        for act in (0.5, 1.0, 2.0):
            print(report(f"保本锁 {act}", rows, act, 0, "breakeven"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
