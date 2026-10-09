"""How much spread is AVAILABLE vs how much do we CAPTURE?

For an entry leg, with mid = engine mid at fill, bb = best bid, fill = our px:
   spread_bp = (mid - fill)/mid * 1e4        <- what we captured
   qpos_bp   = (fill - bb)/mid * 1e4         <- how far above best bid we filled
   => spread_bp + qpos_bp = (mid - bb)/mid*1e4 = HALF-SPREAD available

So the ledger already lets us compute the market's half-spread at fill time.
If half-spread is materially positive while capture ~ 0, we are leaving the
entire available edge on the table -- and THAT is fixable (quote placement),
unlike "market luck".
"""
from __future__ import annotations

import io
import math
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE = "mm_asterdex"


def _t(v):
    n = len(v)
    if n < 2:
        return 0.0, 0.0, 0.0
    mu, sd = st.mean(v), st.pstdev(v)
    se = sd / math.sqrt(n)
    return mu, se, (mu / se if se else 0.0)


def main() -> int:
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT ts, symbol, coalesce(meta_json->>'flatten','false')='true' AS is_exit,
                   coalesce(meta_json->>'side',''), coalesce(spread_bp,0),
                   coalesce(meta_json->>'qpos_bp','0')::float8,
                   coalesce(meta_json->>'qpos','?'),
                   notional, coalesce(net_bp,0)
            FROM lane_ledger
            WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '2026-10-06 02:57:27+08'::timestamptz
            ORDER BY ts
        """)
        rows = cur.fetchall()

    print("=" * 96)
    print("半价差（市场提供） vs 我们实际捕获")
    print("=" * 96)
    for label, want_exit in (("进场腿", False), ("出场腿", True)):
        g = [r for r in rows if r[2] == want_exit]
        if len(g) < 5:
            print(f"  {label}: n={len(g)} 太少")
            continue
        cap = [float(r[4]) for r in g]
        half = [float(r[4]) + float(r[5]) for r in g]
        nets = [float(r[8]) for r in g]
        notl = sum(float(r[7] or 0) for r in g)
        mc, _, tc = _t(cap)
        mh, _, th = _t(half)
        mn, _, tn = _t(nets)
        print()
        print(f"  【{label}】 n={len(g)}  名义 ${notl:,.0f}")
        print(f"    实际捕获 spread_bp   : 均值 {mc:+7.2f}bp  t {tc:+6.2f}")
        print(f"    可得半价差 spread+qpos: 均值 {mh:+7.2f}bp  t {th:+6.2f}")
        print(f"    净额 net_bp          : 均值 {mn:+7.2f}bp  t {tn:+6.2f}")
        if abs(mh) > 1e-9:
            print(f"    **捕获占可得比例     : {mc/mh*100:6.1f}%**")
        # dollarised
        print(f"    若捕获率提到 100%：本组可多赚 "
              f"${(mh-mc)*notl/1e4:+,.2f}")

    print()
    print("=" * 96)
    print("按 qpos 分组（我们的报价位置）")
    print("=" * 96)
    print(f"  {'qpos':<10}{'n':>5}{'capture':>10}{'half_spread':>13}{'net_bp':>10}")
    groups: dict = {}
    for r in rows:
        groups.setdefault(str(r[6]), []).append(r)
    for k in sorted(groups, key=lambda x: -len(groups[x])):
        g = groups[k]
        if len(g) < 3:
            continue
        print(f"  {k:<10}{len(g):>5}{st.mean([float(x[4]) for x in g]):>10.2f}"
              f"{st.mean([float(x[4])+float(x[5]) for x in g]):>13.2f}"
              f"{st.mean([float(x[8]) for x in g]):>10.2f}")

    print()
    print("=" * 96)
    print("判定")
    print("=" * 96)
    print("  · 若『可得半价差』明显 >0 而『捕获』≈0 ⇒ **报价位置还有空间**，")
    print("    这是**可修**的执行问题（把报价挪到更靠边），不是行情运气。")
    print("  · 若两者都≈0 ⇒ 市场本身没有价差可赚，属结构性问题。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
