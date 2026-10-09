"""Is the 80.7% forced-liquidation harm systemic, or a few tail legs?

rule 9 (1) reads: flatten legs net / gross profit < 50%.
Current: -168.12 / +208.41 = 80.7%  -> FAIL

But the worst legs are $68,225 / $67,870 notional dated 10-05 23:2x, i.e. from
the UNCONTROLLED-APERTURE era (before the size caps were understood). If the
ratio is dominated by a handful of that era's legs, then the number describes
history, not the current strategy.

This splits the harm:
  A) all flatten legs
  B) excluding the top-3 worst
  C) only legs AFTER the aperture was opened (18:13)
"""
from __future__ import annotations

import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE = "mm_asterdex"
ERA = "2026-10-05 15:57:56+08"
OPEN = "2026-10-06 18:13:09+08"


def main() -> int:
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT ts, symbol, notional, coalesce(net_bp,0),
                   coalesce(meta_json->>'exit_path','(none)'),
                   coalesce(meta_json->>'flatten','false')='true' AS is_fl,
                   notional*coalesce(net_bp,0)/1e4 AS usd
            FROM lane_ledger
            WHERE lane_id='{LANE}' AND event='fill' AND ts >= '{ERA}'::timestamptz
            ORDER BY ts
        """)
        rows = cur.fetchall()

    def report(label, subset):
        if not subset:
            print(f"  {label:<38} 无样本")
            return
        fl = sum(r[6] for r in subset if r[5])
        net = sum(r[6] for r in subset)
        gross = max(net, 0.0)
        ratio = abs(min(0.0, fl)) / gross * 100 if gross > 0 else float("nan")
        n_fl = sum(1 for r in subset if r[5])
        print(f"  {label:<38} 腿{len(subset):>4} 强平{n_fl:>4} "
              f"强平${fl:>9.2f} 毛利${net:>9.2f} 占比{ratio:>6.1f}%")

    print("=" * 100)
    print("强平腿危害：是系统性的，还是少数尾部腿？")
    print("=" * 100)
    report("A) 全窗口（规矩第 9 条① 的口径）", rows)

    fl_rows = sorted([r for r in rows if r[5]], key=lambda r: r[6])
    for k in (1, 3, 5, 10):
        drop = {id(r) for r in fl_rows[:k]}
        report(f"B) 剔除最差 {k} 条强平腿", [r for r in rows if id(r) not in drop])

    post = [r for r in rows if r[0].strftime("%Y-%m-%d %H:%M:%S") >= "2026-10-06 18:13:09"]
    report("C) 仅开闸后（18:13 起）", post)

    print()
    print("=" * 100)
    print("最差强平腿（含时间，判断属于哪个时代）")
    print("=" * 100)
    print(f"  {'ts':<14}{'sym':<9}{'notional':>12}{'net_bp':>9}{'usd':>11}  exit_path")
    for r in fl_rows[:8]:
        print(f"  {r[0].strftime('%m-%d %H:%M'):<14}{str(r[1])[:8]:<9}"
              f"{float(r[2]):>12,.0f}{float(r[3]):>9.1f}{float(r[6]):>11.3f}  {r[4]}")

    n_big = sum(1 for r in fl_rows if float(r[2]) >= 10000)
    big_usd = sum(r[6] for r in fl_rows if float(r[2]) >= 10000)
    print()
    print(f"  强平腿中 notional >= $10k 的有 {n_big} 条，合计 ${big_usd:+.2f}")
    print(f"  强平腿总亏损 ${sum(r[6] for r in fl_rows):+.2f}")
    if big_usd < 0:
        tot = sum(r[6] for r in fl_rows)
        print(f"  ⇒ 其中 {abs(big_usd)/max(abs(tot),1e-9)*100:.1f}% 来自这些**巨腿**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
