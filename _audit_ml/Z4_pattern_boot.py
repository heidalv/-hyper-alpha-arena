# -*- coding: utf-8 -*-
"""目标指标的 bootstrap（Z4）：模式率（峰值≥0.5% 后亏损）差异 + 上线后新成交核查。"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, load_klines, pick, learned_ok_prod  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
N_BOOT = 4000
random.seed(20260910)


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, timeframe_tier, entry_price, original_size, size,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   close_reason, opened_at, closed_at, status
            from paper_positions
            where timeframe_tier in ('mid','long')
              and (status='closed' and opened_at >= '2026-07-01'
                   or status='open')
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
        peak = float(p["peak_pnl_pct"] or 0) * 100
        rows.append({
            "tier": str(p["timeframe_tier"]), "symbol": sym,
            "pct": usd / (entry * sz0) * 100, "usd": usd,
            "pattern": 1.0 if (peak >= 0.5 and usd < 0) else 0.0,
            "allow": learned_ok_prod(reg_arr[i], pos_arr[i], chg_arr[i]),
            "status": str(p["status"]), "opened": str(p["opened_at"])[:19],
            "reason": str(p["close_reason"] or "?")[:26], "peak": peak,
        })

    closed = [r for r in rows if r["status"] == "closed"]
    print("=== 模式率差异 bootstrap（近 7–9 月已平仓）===")
    for tier in ("mid", "long"):
        sub = [r for r in closed if r["tier"] == tier]
        a = [r for r in sub if r["allow"]]
        b = [r for r in sub if not r["allow"]]
        if not a or not b:
            continue
        na, nb = len(a), len(b)
        diffs = []
        for _ in range(N_BOOT):
            ra = sum(a[random.randrange(na)]["pattern"] for _ in range(na)) / na
            rb = sum(b[random.randrange(nb)]["pattern"] for _ in range(nb)) / nb
            diffs.append(ra - rb)
        diffs.sort()
        obs = sum(r["pattern"] for r in a) / na - sum(r["pattern"] for r in b) / nb
        lo, hi = diffs[int(0.025 * N_BOOT)], diffs[int(0.975 * N_BOOT)]
        sig = "显著" if lo > 0 or hi < 0 else "不显著(跨0)"
        print(f"  {tier:<5} 放行n={na:>3} 模式率={sum(r['pattern'] for r in a)/na:.3f} | "
              f"拦截n={nb:>3} 模式率={sum(r['pattern'] for r in b)/nb:.3f} | "
              f"差={obs:+.3f} CI[{lo:+.3f},{hi:+.3f}] {sig}")

    print("\n=== 上线后新成交（opened_at >= 2026-09-09 17:00）===")
    live = [r for r in rows if r["opened"] >= "2026-09-09 17:00"]
    if not live:
        print("  暂无（门/锁生效后尚无新成交）")
    for r in live:
        print(f"  {r['opened']} {r['symbol']:<8} {r['tier']:<5} {r['status']:<7} "
              f"门={'放行' if r['allow'] else '拦截'} 峰值={r['peak']:>+5.2f}% "
              f"USD={r['usd']:>+8.2f} {r['reason']}")
    print("\n=== 当前未平仓（门/锁配置生效中）===")
    for r in [x for x in rows if x["status"] == "open"]:
        print(f"  {r['opened']} {r['symbol']:<8} {r['tier']:<5} "
              f"门={'放行' if r['allow'] else '拦截'} 峰值={r['peak']:>+5.2f}% USD={r['usd']:>+8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
