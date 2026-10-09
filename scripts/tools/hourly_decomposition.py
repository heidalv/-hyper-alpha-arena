"""Hourly P&L decomposition: is a bad hour systematic or tail-driven?

Answers "are we losing consistently, or occasionally?" by breaking the
last N hours into (count / spread$ / price$ / fee$ / net$) and flagging
where the loss concentrates.
"""
from __future__ import annotations

import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE = "mm_asterdex"


def main() -> int:
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT to_char(date_trunc('hour', ts AT TIME ZONE 'Asia/Shanghai'),
                           'MM-DD HH24:00') hr,
                   count(*) n,
                   count(*) FILTER (WHERE fee_bp < -0.5) taker,
                   coalesce(sum(notional*spread_bp/1e4),0) sp_usd,
                   coalesce(sum(notional*price_bp/1e4),0) px_usd,
                   coalesce(sum(notional*fee_bp/1e4),0) fee_usd,
                   coalesce(sum(notional*net_bp/1e4),0) net_usd,
                   coalesce(sum(notional),0) notl
            FROM lane_ledger
            WHERE lane_id='{LANE}' AND event='fill'
              AND ts > now() - interval '12 hours'
            GROUP BY 1 ORDER BY 1 DESC
        """)
        rows = cur.fetchall()

    print("=" * 100)
    print("逐小时盈亏分解（最近 12 小时）")
    print("=" * 100)
    print(f"  {'hour':<12}{'n':>4}{'taker':>6}{'notional':>12}"
          f"{'spread$':>10}{'price$':>10}{'fee$':>9}{'NET$':>10}")
    tot = {"sp": 0.0, "px": 0.0, "fee": 0.0, "net": 0.0, "n": 0}
    for hr, n, tk, sp, px, fee, net, notl in rows:
        sp, px, fee, net, notl = (float(sp), float(px), float(fee),
                                  float(net), float(notl))
        tot["sp"] += sp
        tot["px"] += px
        tot["fee"] += fee
        tot["net"] += net
        tot["n"] += int(n)
        print(f"  {hr:<12}{n:>4}{int(tk or 0):>6}{notl:>12,.0f}"
              f"{sp:>10.2f}{px:>10.2f}{fee:>9.2f}{net:>10.2f}")
    print("-" * 100)
    print(f"  {'TOTAL':<12}{tot['n']:>4}{'':>6}{'':>12}"
          f"{tot['sp']:>10.2f}{tot['px']:>10.2f}{tot['fee']:>9.2f}"
          f"{tot['net']:>10.2f}")

    bad = [r for r in rows if float(r[6]) < 0]
    good = [r for r in rows if float(r[6]) >= 0]
    print()
    print("=" * 100)
    print("好小时 vs 坏小时")
    print("=" * 100)
    print(f"  盈利小时 {len(good):>2} 个  合计 ${sum(float(r[6]) for r in good):+.2f}")
    print(f"  亏损小时 {len(bad):>2} 个  合计 ${sum(float(r[6]) for r in bad):+.2f}")
    if bad:
        print()
        print(f"  {'hour':<12}{'n':>4}{'spread$':>10}{'price$':>10}{'fee$':>9}{'NET$':>10}")
        for r in sorted(bad, key=lambda x: float(x[6])):
            print(f"  {r[0]:<12}{r[1]:>4}{float(r[3]):>10.2f}{float(r[4]):>10.2f}"
                  f"{float(r[5]):>9.2f}{float(r[6]):>10.2f}")
        worst = min(bad, key=lambda x: float(x[6]))
        print()
        print(f"  最差小时 {worst[0]} 贡献 ${float(worst[6]):+.2f}"
              f" = 全部亏损的 {abs(float(worst[6]))/max(abs(sum(float(r[6]) for r in bad)),1e-9)*100:.0f}%")
        # 该小时是不是被单腿主导
        with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
            cur.execute(f"""
                SELECT symbol, notional, coalesce(net_bp,0),
                       notional*coalesce(net_bp,0)/1e4 usd
                FROM lane_ledger
                WHERE lane_id='{LANE}' AND event='fill'
                  AND to_char(date_trunc('hour', ts AT TIME ZONE 'Asia/Shanghai'),
                              'MM-DD HH24:00') = %s
                ORDER BY usd ASC LIMIT 4
            """, (worst[0],))
            legs = cur.fetchall()
        print(f"  该小时最差 4 条腿：")
        for sym, notl, bp, usd in legs:
            print(f"    {str(sym)[:10]:<12}{float(notl):>12,.0f}"
                  f"{float(bp):>9.2f}bp{float(usd):>11.4f}")
    print()
    print("  读法：")
    print("   · 若 spread 项为正、price 项为负 ⇒ 报价质量没问题，亏在行情")
    print("   · 若坏小时被 1 条腿主导 ⇒ 尾部事件，不是系统性亏损")
    print("   · 若某小时 notional 异常巨大 ⇒ 那是**规模变更**在混淆结论，"
          "必须先按规模切时代再比较")

    # ── [T49] 按**规模时代**切分：稳定 5% 档 vs 无约束/巨腿档 ──
    # 动机：上表里"盈利小时"的 notional 是 $1.5M / $211k（旧的无约束巨腿档），
    # 而"亏损小时"是 ~$500/腿（当前的 5% 档）。**直接比小时等于比两个不同的策略。**
    print()
    print("=" * 100)
    print("按规模时代切分（这才是可比的）")
    print("=" * 100)
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT CASE WHEN notional < 1000 THEN 'A 稳定档 (<$1k)'
                        WHEN notional < 10000 THEN 'B 中档 ($1k-10k)'
                        ELSE 'C 巨腿档 (>=$10k)' END era,
                   count(*) n,
                   round(coalesce(sum(notional),0)::numeric,0) notl,
                   round(coalesce(sum(notional*spread_bp/1e4),0)::numeric,4) sp,
                   round(coalesce(sum(notional*price_bp/1e4),0)::numeric,4) px,
                   round(coalesce(sum(notional*fee_bp/1e4),0)::numeric,4) fee,
                   round(coalesce(sum(notional*net_bp/1e4),0)::numeric,4) net
            FROM lane_ledger
            WHERE lane_id='{LANE}' AND event='fill'
              AND ts > now() - interval '12 hours'
            GROUP BY 1 ORDER BY 1
        """)
        eras = cur.fetchall()
    print(f"  {'era':<20}{'n':>5}{'notional':>14}{'spread$':>11}{'price$':>11}"
          f"{'fee$':>9}{'NET$':>11}")
    for era, n, notl, sp, px, fee, net in eras:
        print(f"  {era:<20}{n:>5}{float(notl):>14,.0f}{float(sp):>11.2f}"
              f"{float(px):>11.2f}{float(fee):>9.2f}{float(net):>11.2f}")
    print()
    print("  ⇒ 只有 **A 稳定档**代表当前策略；B/C 是规模变更期的产物，")
    print("    把它们混在一起看小时盈亏会得出错误结论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
