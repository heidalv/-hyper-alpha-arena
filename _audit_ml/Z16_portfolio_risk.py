# -*- coding: utf-8 -*-
"""Z16：组合层「入场时已承担风险」——是否在风险拥挤时入场会更容易大亏？

背景：mid 层初始 SL 固定 4.5%、long 层 6.5%（Z9b 实测）。若同时持有 N 笔，
则账户在入场瞬间已承担 Σ(名义×SL%) 的风险。若这些风险同向（多头策略），
一次市场回落会让多笔同时回吐——这正是「先盈利后大亏」的组合层放大器。

检验：
A. 逐笔计算「入场时刻的已承担风险 / 权益」分布（用固定初始 SL 重建，避免活体字段）；
B. 按风险档位看后续表现（均值%、胜率、模式率）；
C. 反事实：入场时若已承担风险将超过 X% 权益则跳过 → 总 USD / 逐月 / bootstrap。
"""
from __future__ import annotations

import os
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
SEED = 20260910
EQUITY = 4800.0          # acct=1 当前权益（Z14）
SL_PCT = {"mid": 4.5, "long": 6.5}


def build(days=75):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, timeframe_tier, entry_price, size, original_size,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
    recs = []
    for p in rows:
        sz0 = float(p["original_size"] or p["size"] or 0)
        entry = float(p["entry_price"] or 0)
        if sz0 <= 0 or entry <= 0 or not p["opened_at"] or not p["closed_at"]:
            continue
        recs.append({
            "id": p["id"], "symbol": p["symbol"], "tier": str(p["timeframe_tier"]),
            "notional0": sz0 * entry,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "db_peak": float(p["peak_pnl_pct"] or 0) * 100.0,
            "open_ts": int(p["opened_at"].timestamp()),
            "close_ts": int(p["closed_at"].timestamp()),
            "mon": str(p["opened_at"])[:7],
        })
    recs.sort(key=lambda r: r["open_ts"])
    return recs


def risk_at_entry(r, recs):
    """入场瞬间已承担风险（含本笔）占权益比例 %。"""
    tot = r["notional0"] * SL_PCT.get(r["tier"], 5.0) / 100.0
    for o in recs:
        if o is r or o["open_ts"] >= r["open_ts"] or o["close_ts"] <= r["open_ts"]:
            continue
        tot += o["notional0"] * SL_PCT.get(o["tier"], 5.0) / 100.0
    return tot / EQUITY * 100.0


def main() -> int:
    recs = build(75)
    base = sum(r["base_usd"] for r in recs)
    print(f"样本 n={len(recs)} 基线=${base:+.2f} 权益假设=${EQUITY:.0f}")
    for r in recs:
        r["risk_pct"] = risk_at_entry(r, recs)

    vals = sorted(r["risk_pct"] for r in recs)
    print(f"\n===== A. 入场时已承担风险（占权益%）=====")
    print(f"  中位={vals[len(vals)//2]:.2f}%  p90={vals[int(0.9*len(vals))]:.2f}%  "
          f"最大={vals[-1]:.2f}%  最小={vals[0]:.2f}%")

    print("\n===== B. 风险档位 → 后续表现 =====")
    print(f"{'风险档':<16}{'n':>5}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}{'≤-3%':>7}")
    buckets = [(0, 4), (4, 8), (8, 12), (12, 16), (16, 20), (20, 30), (30, 999)]
    for lo, hi in buckets:
        sub = [r for r in recs if lo <= r["risk_pct"] < hi]
        if not sub:
            continue
        pcts = [r["base_usd"] / r["notional0"] * 100 for r in sub]
        pat = sum(1 for r in sub if r["db_peak"] >= 0.5 and r["base_usd"] < 0)
        print(f"[{lo},{hi})%{'':<8}{len(sub):>5}{sum(r['base_usd'] for r in sub):>+10.2f}"
              f"{sum(pcts)/len(pcts):>+9.3f}{sum(1 for x in pcts if x>0)/len(pcts):>7.3f}"
              f"{pat/len(sub):>8.3f}{sum(1 for x in pcts if x<=-3):>7}")

    print("\n===== C. 反事实：入场时风险将超过 X% 权益则跳过 =====")
    print(f"{'方案':<20}{'保留n':>7}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}  逐月USD")
    for cap in (8, 12, 16, 20, 25):
        keep, skip = [], []
        for r in recs:
            (skip if r["risk_pct"] > cap else keep).append(r)
        usds = [r["base_usd"] for r in keep]
        pcts = [u / r["notional0"] * 100 for u, r in zip(usds, keep)]
        bym = defaultdict(float)
        for u, r in zip(usds, keep):
            bym[r["mon"]] += u
        bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
        pat = sum(1 for r in keep if r["db_peak"] >= 0.5 and r["base_usd"] < 0)
        print(f"{f'风险上限 {cap}%':<20}{len(keep):>7}{sum(usds):>+10.2f}"
              f"{sum(pcts)/len(pcts):>+9.3f}{sum(1 for x in pcts if x>0)/len(pcts):>7.3f}"
              f"{pat/len(keep):>8.3f}  {bym_s}")
        d = [r["base_usd"] / r["notional0"] * 100 for r in skip]
        if len(d) >= 5:
            rnd = random.Random(SEED)
            n = len(d)
            boots = sorted(sum(d[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
            lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
            print(f"    被跳过 {n} 笔均值={sum(d)/n:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
                  f"{'显著为负（限风险有效）' if hi < 0 else '不显著(跨0)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
