# -*- coding: utf-8 -*-
"""「仅门」下的模式组统计（Y25）：决定锁去留的关键一算。"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, load_klines, pick, learned_ok_prod  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, timeframe_tier, entry_price, close_price, original_size, size,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   close_reason, opened_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '30 days'
            order by opened_at
        """)).fetchall()]
    h1, d1 = load_klines({p["symbol"] for p in poss})
    feats = {}
    for sym in {p["symbol"] for p in poss}:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if s is None or ds is None or len(s) < 300 or len(ds) < 70:
            continue
        feats[sym] = (s, build_bar_features(s, ds))

    rows = []
    for p in poss:
        sym = p["symbol"]
        if sym not in feats:
            continue
        s, (reg_arr, pos_arr, chg_arr) = feats[sym]
        entry = float(p["entry_price"] or 0)
        sz0 = float(p["original_size"] or p["size"] or 0)
        if entry <= 0 or sz0 <= 0:
            continue
        ts = int(p["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
            continue
        usd = (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
               - float(p["partial_fee_paid"] or 0))
        rows.append({
            "tier": str(p["timeframe_tier"]), "symbol": sym,
            "peak": float(p["peak_pnl_pct"] or 0) * 100, "usd": usd,
            "notional0": sz0 * entry,
            "allow": learned_ok_prod(reg_arr[i], pos_arr[i], chg_arr[i]),
            "reason": str(p["close_reason"] or "?")[:26],
            "opened": str(p["opened_at"])[:16],
        })

    print(f"总样本 n={len(rows)}（mid+long，近 30 天）")
    for tier in ("mid", "long", "ALL"):
        sub = rows if tier == "ALL" else [r for r in rows if r["tier"] == tier]
        if not sub:
            continue
        gated = [r for r in sub if r["allow"]]
        for label, grp in (("全量", sub), ("仅门放行", gated)):
            pat = [r for r in grp if r["peak"] >= 0.5 and r["usd"] < 0]
            print(f"\n=== tier={tier} / {label} ===")
            print(f"  n={len(grp):>3} 总USD={sum(r['usd'] for r in grp):>+8.2f} "
                  f"胜率={sum(1 for r in grp if r['usd'] > 0)/max(1,len(grp)):.3f} "
                  f"≤-2%(按名义%)={sum(1 for r in grp if r['usd']/r['notional0']*100 <= -2):>2}")
            print(f"  「浮盈→亏损」: n={len(pat):>3} USD={sum(r['usd'] for r in pat):>+8.2f}")
            for r in sorted(pat, key=lambda x: x["usd"])[:6]:
                print(f"    {r['opened']} {r['symbol']:<8} 峰值={r['peak']:>+5.2f}% "
                      f"USD={r['usd']:>+8.2f} {r['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
