# -*- coding: utf-8 -*-
"""逐月分折验证（Z2）：learned 门的准入价值是否跨月稳定？

背景：门条件（up+chg∈[3,6) / chop pos≥60&chg≥2）与「门只作用 mid」的结论
都来自 8/10–9/9 样本。本脚本把同一套门**逐月**回放到 7/8/9 月成交上，
检验「放行集 vs 拦截集」的分离度是否稳定（过拟合 vs 真实边际）。

口径：总 USD = unrealized + partial_realized − partial_fee；峰值价格口径。
"""
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
            select id, symbol, timeframe_tier, trade_nature, entry_price, close_price,
                   original_size, size, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and opened_at >= '2026-07-01'
            order by opened_at
        """)).fetchall()]
    print(f"样本 n={len(poss)}（2026-07-01 起 mid/long 已平仓）")

    h1, d1 = load_klines({p["symbol"] for p in poss})
    feats = {}
    for sym in {p["symbol"] for p in poss}:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if s is None or ds is None or len(s) < 300 or len(ds) < 70:
            continue
        feats[sym] = (s, build_bar_features(s, ds))
    print(f"K 线可用币种: {len(feats)} / {len({p['symbol'] for p in poss})}")

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
        ntl = entry * sz0
        rows.append({
            "mon": str(p["opened_at"])[:7], "tier": str(p["timeframe_tier"]),
            "symbol": sym, "usd": usd, "notional0": ntl,
            "pct": usd / ntl * 100,
            "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "allow": learned_ok_prod(reg_arr[i], pos_arr[i], chg_arr[i]),
            "regime": reg_arr[i], "chg": round(chg_arr[i], 2), "pos": round(pos_arr[i], 1),
            "reason": str(p["close_reason"] or "?")[:24],
        })

    def agg(rows_, label):
        if not rows_:
            return f"  {label:<30} 无样本"
        n = len(rows_)
        usd = sum(r["usd"] for r in rows_)
        big = sum(1 for r in rows_ if r["pct"] <= -2)
        pat = [r for r in rows_ if r["peak"] >= 0.5 and r["usd"] < 0]
        return (f"  {label:<30} n={n:>3} 总USD={usd:>+9.2f} 均值={sum(r['pct'] for r in rows_)/n:>+7.3f}% "
                f"胜率={sum(1 for r in rows_ if r['usd'] > 0)/n:>5.3f} 大亏={big:>2} "
                f"模式={len(pat):>2}笔/{sum(r['usd'] for r in pat):>+8.2f}")

    months = sorted({r["mon"] for r in rows})
    for tier in ("mid", "long"):
        print(f"\n===== tier={tier} =====")
        for mon in months:
            sub = [r for r in rows if r["tier"] == tier and r["mon"] == mon]
            if not sub:
                continue
            print(f"\n[{mon}]")
            print(agg(sub, "全部"))
            print(agg([r for r in sub if r["allow"]], "门放行"))
            print(agg([r for r in sub if not r["allow"]], "门拦截"))
        # 全期汇总
        allsub = [r for r in rows if r["tier"] == tier]
        print(f"\n[全期 7–9月]")
        print(agg(allsub, "全部"))
        print(agg([r for r in allsub if r["allow"]], "门放行"))
        print(agg([r for r in allsub if not r["allow"]], "门拦截"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
