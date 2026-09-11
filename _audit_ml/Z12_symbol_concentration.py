# -*- coding: utf-8 -*-
"""Z12：模式笔的**标的集中度**与「同币种重复入场」效应（组合层，未测过）。

观察：Z7 的模式笔清单里 VIRTUAL 出现 8 次（-$65.84，占模式总亏 30%）、
UNI/SOL/XRP 各 3 次 —— 亏损高度集中在少数币种。若机制是「同一标的处于
持续不利状态，系统反复入场」，则**同币种冷却**（亏损后 N 小时内不再入场）
是可落码、非拟合的组合层杠杆。

检验：
A. 逐标的 n / 总 USD / 模式笔 USD（是否集中）；
B. 「同币种亏损后 Δ 内再入场」vs「全新入场」的均值差（Δ=12/24/48/168h）；
C. 反事实：跳过「亏损后 Δ 内再入场」的笔 → 总 USD / 逐月 / walk-forward / bootstrap。
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
sys.path.insert(0, str(ROOT / "_audit_ml"))

from deep_long_freshness import load_klines  # noqa: E402
from Z11_exit_delay import channel, pick  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
SEED = 20260910


def build(days=75):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, size, original_size,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
    recs = []
    for p in poss:
        sz0 = float(p["original_size"] or p["size"] or 0)
        entry = float(p["entry_price"] or 0)
        if sz0 <= 0 or entry <= 0 or not p["opened_at"]:
            continue
        recs.append({
            "id": p["id"], "symbol": p["symbol"], "tier": str(p["timeframe_tier"]),
            "notional0": sz0 * entry,
            "base_usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                         - float(p["partial_fee_paid"] or 0)),
            "db_peak": float(p["peak_pnl_pct"] or 0) * 100.0,
            "open_ts": int(p["opened_at"].timestamp()),
            "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else None,
            "mon": str(p["opened_at"])[:7],
            "ch": channel(str(p["close_reason"] or "")),
        })
    recs.sort(key=lambda r: r["open_ts"])
    return recs


def med(xs):
    v = sorted(xs)
    return v[len(v) // 2] if v else float("nan")


def main() -> int:
    recs = build(75)
    print(f"样本 n={len(recs)}（近 75 天 mid+long，按开仓时间排序）")
    print(f"基线总 USD=${sum(r['base_usd'] for r in recs):+.2f}")

    # ---------- A. 逐标的集中度 ----------
    bysym = defaultdict(list)
    for r in recs:
        bysym[r["symbol"]].append(r)
    print("\n===== A. 逐标的（n≥3，按总 USD 升序）=====")
    print(f"{'symbol':<10}{'n':>4}{'总USD':>10}{'均值USD':>10}{'模式笔n':>8}{'模式USD':>10}"
          f"{'胜率':>7}")
    rows = []
    for sym, sub in bysym.items():
        usd = sum(r["base_usd"] for r in sub)
        pat = [r for r in sub if r["db_peak"] >= 0.5 and r["base_usd"] < 0]
        rows.append((usd, sym, sub, pat))
    for usd, sym, sub, pat in sorted(rows):
        if len(sub) < 3:
            continue
        print(f"{sym:<10}{len(sub):>4}{usd:>+10.2f}{usd/len(sub):>+10.2f}"
              f"{len(pat):>8}{sum(r['base_usd'] for r in pat):>+10.2f}"
              f"{sum(1 for r in sub if r['base_usd']>0)/len(sub):>7.3f}")
    tot = sum(r["base_usd"] for r in recs)
    top5 = sum(usd for usd, _s, _sub, _p in sorted(rows)[:5])
    print(f"  最差 5 个标的合计 ${top5:+.2f}（占全量 {top5/tot*100 if tot else 0:.0f}% 的亏损来源）")

    # ---------- B. 同币种「亏损后再入场」 ----------
    print("\n===== B. 同币种前一笔已平仓结果 → 本笔表现 =====")
    prev_close = {}
    prev_usd = {}
    tags = {}
    for r in recs:
        sym = r["symbol"]
        tag = "首笔"
        pc = prev_close.get(sym)
        if pc is not None:
            gap_h = (r["open_ts"] - pc) / 3600.0
            prev = prev_usd[sym]
            tag = f"前笔{'亏' if prev <= 0 else '盈'}·{('≤24h' if gap_h <= 24 else '24-72h' if gap_h <= 72 else '>72h')}"
        tags[r["id"]] = tag
        if r["close_ts"]:
            prev_close[sym] = max(prev_close.get(sym) or 0, r["close_ts"])
            prev_usd[sym] = r["base_usd"]
    bytag = defaultdict(list)
    for r in recs:
        bytag[tags[r["id"]]].append(r)
    print(f"{'分组':<20}{'n':>5}{'总USD':>10}{'均值USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}")
    for tag, sub in sorted(bytag.items(), key=lambda x: -len(x[1])):
        pcts = [r["base_usd"] / r["notional0"] * 100 for r in sub]
        pat = sum(1 for r in sub if r["db_peak"] >= 0.5 and r["base_usd"] < 0)
        print(f"{tag:<20}{len(sub):>5}{sum(r['base_usd'] for r in sub):>+10.2f}"
              f"{sum(r['base_usd'] for r in sub)/len(sub):>+10.2f}"
              f"{sum(pcts)/len(pcts):>+9.3f}"
              f"{sum(1 for x in pcts if x>0)/len(pcts):>7.3f}{pat/len(sub):>8.3f}")

    # ---------- C. 反事实：跳过「亏损后 Δ 内再入场」 ----------
    print("\n===== C. 反事实：跳过「同币种亏损后 Δ 内再入场」 =====")
    print(f"{'方案':<24}{'保留n':>7}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}  逐月USD")
    for delta_h in (12, 24, 48, 168):
        keep, skip = [], []
        last = {}  # sym -> (close_ts, usd)
        for r in recs:
            sym = r["symbol"]
            prev = last.get(sym)
            drop = False
            if prev is not None:
                pc, pu = prev
                if pu <= 0 and (r["open_ts"] - pc) / 3600.0 <= delta_h:
                    drop = True
            (skip if drop else keep).append(r)
            if r["close_ts"]:
                if prev is None or r["close_ts"] >= prev[0]:
                    last[sym] = (r["close_ts"], r["base_usd"])
        usds = [r["base_usd"] for r in keep]
        pcts = [u / r["notional0"] * 100 for u, r in zip(usds, keep)]
        bym = defaultdict(float)
        for u, r in zip(usds, keep):
            bym[r["mon"]] += u
        bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
        pat = sum(1 for r in keep if r["db_peak"] >= 0.5 and r["base_usd"] < 0)
        print(f"{f'跳过亏损后{delta_h}h内':<24}{len(keep):>7}{sum(usds):>+10.2f}"
              f"{sum(pcts)/len(pcts):>+9.3f}{sum(1 for x in pcts if x>0)/len(pcts):>7.3f}"
              f"{pat/len(keep):>8.3f}  {bym_s}")
        # bootstrap：被跳过笔的 pct 均值是否显著为负
        d = [r["base_usd"] / r["notional0"] * 100 for r in skip]
        if len(d) >= 5:
            rnd = random.Random(SEED)
            n = len(d)
            boots = sorted(sum(d[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
            lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
            print(f"    被跳过 {n} 笔均值={sum(d)/n:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
                  f"{'显著为负（跳过有效）' if hi < 0 else '不显著(跨0)'}")

    # ---------- D. 同币种并发持仓 ----------
    print("\n===== D. 同币种并发（持仓期重叠）=====")
    conc, single = [], []
    for i, r in enumerate(recs):
        overlap = any(o["symbol"] == r["symbol"] and o is not r
                      and o["open_ts"] < (r["close_ts"] or r["open_ts"])
                      and (o["close_ts"] or 10**12) > r["open_ts"] for o in recs)
        (conc if overlap else single).append(r)
    for label, sub in (("并发", conc), ("单笔", single)):
        if not sub:
            continue
        pcts = [r["base_usd"] / r["notional0"] * 100 for r in sub]
        print(f"  {label} n={len(sub)} 总USD=${sum(r['base_usd'] for r in sub):+.2f} "
              f"均值={sum(pcts)/len(pcts):+.3f}% 胜率={sum(1 for x in pcts if x>0)/len(pcts):.3f} "
              f"模式率={sum(1 for r in sub if r['db_peak']>=0.5 and r['base_usd']<0)/len(sub):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
