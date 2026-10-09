"""Is the C-band (>=10k leg) profit real, or a time-clustered market event?

R064 concluded "all profit comes from 30 legs >=$10k". But if those 30 legs
are clustered in one or two hours, they are NOT 30 independent samples --
they are one market move counted 30 times, and effective n could be ~2.

This tool tests that, plus two confounds:
  (a) time clustering  -> effective sample size
  (b) exit_path mix    -> maybe C band simply contains no taker_stop legs
  (c) symbol mix       -> maybe C band is one or two symbols
"""
from __future__ import annotations

import io
import statistics as st
import sys
from collections import Counter

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE = "mm_asterdex"
ERA = "2026-10-05 15:57:56+08"


def main() -> int:
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT ts, symbol, notional, coalesce(net_bp,0),
                   coalesce(meta_json->>'exit_path','(entry)'),
                   coalesce(meta_json->>'flatten','false'),
                   notional*coalesce(net_bp,0)/1e4 usd
            FROM lane_ledger
            WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '{ERA}'::timestamptz
            ORDER BY ts
        """)
        rows = cur.fetchall()

    bands = {"A <1k": [], "B 1k-10k": [], "C >=10k": []}
    for ts, sym, notl, nb, ep, fl, usd in rows:
        n = float(notl or 0)
        rec = (ts, str(sym), n, float(nb), str(ep), str(fl), float(usd))
        if n < 1000:
            bands["A <1k"].append(rec)
        elif n < 10000:
            bands["B 1k-10k"].append(rec)
        else:
            bands["C >=10k"].append(rec)

    print("=" * 100)
    print("(a) 时间聚集检验 —— C 档的 30 条腿是独立样本吗？")
    print("=" * 100)
    for name, g in bands.items():
        if not g:
            continue
        hrs = Counter(t.strftime("%m-%d %H:00") for t, *_ in g)
        days = len(set(t.strftime("%m-%d") for t, *_ in g))
        print(f"  {name:<10} n={len(g):>4}  跨 {days} 天, {len(hrs)} 个不同小时")
        top = hrs.most_common(4)
        share = sum(v for _, v in top) / len(g) * 100
        print(f"             最集中的 4 个小时占 {share:.0f}%: "
              + ", ".join(f"{h}={v}" for h, v in top))

    print()
    print("=" * 100)
    print("(b) 出场路径构成 —— C 档是不是「恰好没有亏损路径」？")
    print("=" * 100)
    for name, g in bands.items():
        if not g:
            continue
        c2 = Counter(r[4] for r in g)
        print(f"  {name:<10} " + "  ".join(f"{k}={v}" for k, v in c2.most_common(6)))

    print()
    print("=" * 100)
    print("(c) 币种构成 —— C 档是不是集中在少数币？")
    print("=" * 100)
    for name, g in bands.items():
        if not g:
            continue
        c3 = Counter(r[1] for r in g)
        uniq = len(c3)
        top = c3.most_common(3)
        print(f"  {name:<10} {uniq} 个币, top: "
              + ", ".join(f"{s}={v}" for s, v in top))

    print()
    print("=" * 100)
    print("(d) 按「小时」聚合后重算 —— 用小时作为独立单位")
    print("=" * 100)
    for name, g in bands.items():
        if not g:
            continue
        per_hour: dict = {}
        for ts, sym, n, nb, ep, fl, usd in g:
            h = ts.strftime("%m-%d %H:00")
            per_hour.setdefault(h, 0.0)
            per_hour[h] += usd
        vals = list(per_hour.values())
        if len(vals) < 2:
            print(f"  {name:<10} 只有 {len(vals)} 个小时 ⇒ **无法计算小时级显著性**")
            continue
        mu, sd = st.mean(vals), st.pstdev(vals)
        se = sd / (len(vals) ** 0.5)
        t = mu / se if se else 0
        print(f"  {name:<10} 小时数={len(vals):>2}  小时均值=${mu:+.2f}"
              f"  SD=${sd:.2f}  **t={t:+.2f}**  {'显著' if abs(t)>=1.96 else '不显著'}")

    print()
    print("=" * 100)
    print("判决")
    print("=" * 100)
    cg = bands["C >=10k"]
    if cg:
        hrs = Counter(t.strftime("%m-%d %H:00") for t, *_ in cg)
        top2 = sum(v for _, v in hrs.most_common(2))
        print(f"  C 档 {len(cg)} 条腿中，最集中的 2 个小时占 {top2} 条 "
              f"({top2/len(cg)*100:.0f}%)")
        if top2 / len(cg) > 0.6:
            print("  ⚠️ C 档利润高度**时间聚集** ⇒ 不是 30 个独立样本，")
            print("     很可能是**1~2 次行情**被记了 30 次。")
            print("     ⇒ R064 的「钱只在大额腿上」**证据强度被高估**，需要更正。")
        else:
            print("  ✅ C 档分布在不同小时 ⇒ 独立样本较多，结论相对可信")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
