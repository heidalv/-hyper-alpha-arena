# -*- coding: utf-8 -*-
"""long 层被 learned 门拦掉的赢家（Y26）：门是否误伤趋势车道。"""
from __future__ import annotations

import os
import sys
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
            select id, symbol, timeframe_tier, trade_nature, entry_price, close_price,
                   original_size, size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at
            from paper_positions
            where timeframe_tier='long' and status='closed'
              and closed_at >= now() - interval '60 days'
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
            "symbol": sym, "usd": usd, "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "regime": reg_arr[i], "chg": round(chg_arr[i], 2), "pos": round(pos_arr[i], 1),
            "allow": learned_ok_prod(reg_arr[i], pos_arr[i], chg_arr[i]),
            "opened": str(p["opened_at"])[:16], "nature": str(p["trade_nature"]),
        })

    print(f"long 层(60天) n={len(rows)}")
    print(f"\n{'时间':<17}{'币':<8}{'regime':<6}{'chg24':>7}{'pos24':>7}{'峰值':>7}{'USD':>9}  门")
    for r in sorted(rows, key=lambda x: -x["usd"]):
        print(f"{r['opened']:<17}{r['symbol']:<8}{r['regime']:<6}{r['chg']:>+7.2f}{r['pos']:>7.1f}"
              f"{r['peak']:>+7.2f}{r['usd']:>+9.2f}  {'放行' if r['allow'] else '拦截'}")

    gated = [r for r in rows if r["allow"]]
    blocked = [r for r in rows if not r["allow"]]
    print(f"\n放行 n={len(gated)} USD={sum(r['usd'] for r in gated):+.2f} | "
          f"拦截 n={len(blocked)} USD={sum(r['usd'] for r in blocked):+.2f}")
    print(f"被拦的盈利单: {sum(1 for r in blocked if r['usd'] > 0)} 笔，"
          f"合计 +{sum(r['usd'] for r in blocked if r['usd'] > 0):.2f}")
    print(f"被拦的亏损单: {sum(1 for r in blocked if r['usd'] < 0)} 笔，"
          f"合计 {sum(r['usd'] for r in blocked if r['usd'] < 0):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
