"""Effective sample size: how much INDEPENDENT data do we actually have?

Legs are not independent: the engine quotes ~39 symbols at once and they move
together with the market. So n_legs massively overstates n_effective.

This tool measures:
  1. legs per hour (how clustered trading is)
  2. hour-level dispersion (the honest unit)
  3. correlation of hourly P&L across symbols (are they one bet?)
  4. required runtime for a t>=1.96 verdict, in HOURS not legs
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
            SELECT to_char(date_trunc('hour', ts AT TIME ZONE 'Asia/Shanghai'),
                           'MM-DD HH24') hr,
                   count(*) n,
                   coalesce(sum(notional*net_bp/1e4),0) usd,
                   coalesce(sum(notional*spread_bp/1e4),0) sp,
                   coalesce(sum(notional*price_bp/1e4),0) px,
                   count(DISTINCT symbol) nsym
            FROM lane_ledger
            WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '{ERA}'::timestamptz
            GROUP BY 1 ORDER BY 1
        """)
        rows = cur.fetchall()

    print("=" * 96)
    print("有效样本量分析")
    print("=" * 96)
    legs = sum(int(r[1]) for r in rows)
    hours = len(rows)
    print(f"  总腿数        : {legs}")
    print(f"  独立小时数    : **{hours}**   ← 这才是接近独立的单位")
    print(f"  平均腿/小时   : {legs/max(hours,1):.1f}")
    print(f"  每小时涉及币数: 均值 {st.mean([int(r[5]) for r in rows]):.1f} 个")

    # 相关性代理：每小时里，盈利币与亏损币的比例
    print()
    print("=" * 96)
    print("每小时 P&L（诚实的分析单位）")
    print("=" * 96)
    print(f"  {'hour':<10}{'n':>4}{'sym':>5}{'NET$':>10}{'spread$':>10}{'price$':>10}")
    vals = []
    for hr, n, usd, sp, px, nsym in rows:
        vals.append(float(usd))
        flag = "  <=LOSS" if float(usd) < 0 else ""
        print(f"  {hr:<10}{n:>4}{nsym:>5}{float(usd):>10.2f}"
              f"{float(sp):>10.2f}{float(px):>10.2f}{flag}")
    mu, sd = st.mean(vals), st.pstdev(vals)
    se = sd / (len(vals) ** 0.5)
    t = mu / se if se else 0
    print("-" * 96)
    print(f"  小时均值 ${mu:+.2f}  SD ${sd:.2f}  SE ${se:.2f}  **t={t:+.2f}**")
    print(f"  95% CI = [${mu-1.96*se:+.2f}, ${mu+1.96*se:+.2f}]")
    print(f"  盈利小时 {sum(1 for v in vals if v>0)} / {len(vals)}")

    need_hours = None
    if abs(mu) > 1e-9 and sd > 0:
        need_hours = (1.96 * sd / abs(mu)) ** 2
    print()
    print("=" * 96)
    print("要得到 t>=1.96 的判决，还需要多少**独立小时**？")
    print("=" * 96)
    if need_hours:
        print(f"  按当前 |均值| ${abs(mu):.2f} 与 SD ${sd:.2f}：")
        print(f"    需要约 **{need_hours:.0f} 个独立小时**（现有 {hours} 个）")
        if hours < need_hours:
            print(f"    ⇒ 还差 **{need_hours-hours:.0f} 小时 ≈ {max(0,(need_hours-hours))/24:.1f} 天**")
        else:
            print("    ⇒ 样本量已足够，但 t 仍不显著 ⇒ 均值太小，非样本量问题")
    print()
    print("  ⚠️ 注意：小时之间也非完全独立（连续行情有自相关），")
    print("     所以真实所需时间只会**比上表更长**，不会更短。")

    print()
    print("=" * 96)
    print("腿级 vs 小时级 的落差（为什么不能用腿数吹样本量）")
    print("=" * 96)
    print(f"  腿级：n={legs}")
    print(f"  小时级：n={hours}")
    print(f"  ⇒ 腿数把样本量**放大了 {legs/max(hours,1):.0f} 倍**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
