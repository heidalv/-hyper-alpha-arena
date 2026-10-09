"""Era split restricted to the POST-FIX window.

The 12h window mixes pre-fix history (when the exit paths were bleeding).
This restricts to legs after the fixes landed, so the number describes the
CURRENT strategy, and splits by notional era so scale changes don't confound.
"""
from __future__ import annotations

import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE = "mm_asterdex"
ERA = "2026-10-05 15:57:56+08"


def main() -> int:
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        for label, since in (("修复后全窗口", ERA),
                             ("仅稳定档纪元 (>=06:00)", "2026-10-06 06:00:00+08")):
            cur.execute(f"""
                SELECT CASE WHEN notional < 1000 THEN 'A 稳定档 (<$1k)'
                            WHEN notional < 10000 THEN 'B 中档 ($1k-10k)'
                            ELSE 'C 巨腿档 (>=$10k)' END era,
                       count(*) n,
                       round(coalesce(sum(notional),0)::numeric,0) notl,
                       round(coalesce(sum(notional*spread_bp/1e4),0)::numeric,4) sp,
                       round(coalesce(sum(notional*price_bp/1e4),0)::numeric,4) px,
                       round(coalesce(sum(notional*net_bp/1e4),0)::numeric,4) net,
                       round(coalesce(avg(net_bp),0)::numeric,2) avg_bp
                FROM lane_ledger
                WHERE lane_id='{LANE}' AND event='fill'
                  AND ts >= '{since}'::timestamptz
                GROUP BY 1 ORDER BY 1
            """)
            rows = cur.fetchall()
            print("=" * 96)
            print(f"{label}   (since {since})")
            print("=" * 96)
            print(f"  {'era':<20}{'n':>5}{'notional':>13}{'spread$':>11}"
                  f"{'price$':>11}{'NET$':>11}{'avgNet_bp':>11}")
            for era, n, notl, sp, px, net, abp in rows:
                print(f"  {era:<20}{n:>5}{float(notl):>13,.0f}{float(sp):>11.2f}"
                      f"{float(px):>11.2f}{float(net):>11.2f}{float(abp):>11.2f}")
            tot = sum(float(r[5]) for r in rows)
            n_t = sum(int(r[1]) for r in rows)
            print(f"  {'合计':<20}{n_t:>5}{'':>13}{'':>11}{'':>11}{tot:>11.2f}")
            print()

    print("=" * 96)
    print("结论")
    print("=" * 96)
    print("  A 稳定档 = 当前策略的真实表现（5% 单腿上限生效后）")
    print("  C 巨腿档 = 规模变更期产物，**不代表当前策略**")
    print("  若 A 为负而 C 为正 ⇒ 盈利完全依赖'超大单腿'，不是可复制的 edge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
