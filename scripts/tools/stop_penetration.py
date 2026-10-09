"""Do stops actually hold? The risk model's sizing assumes they do.

Observation: current-era legs exist at -422bp (SI, $481) and -298bp (LYN, $488),
while `disaster_stop_bp(vol, floor=15, cap=40)` should cap the loss near 15-60bp.

If legs routinely lose far beyond the stop cap, then:
  · the risk model's `notional_cap_usd(equity, stop_bp, ...)` UNDER-estimates risk
  · opening the aperture (sizing from that model) is more dangerous than it looks

This measures the loss distribution vs the stop cap.
"""
from __future__ import annotations

import io
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE = "mm_asterdex"
# current (controlled-aperture) era
CUT = "2026-10-06 02:57:27+08"
STOP_CAP = 60.0     # disaster_stop_bp cap_bp (learned param upper bound)


def main() -> int:
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT ts, symbol, notional, coalesce(net_bp,0),
                   coalesce(meta_json->>'exit_path','(entry)'),
                   coalesce(meta_json->>'flatten','false')='true' AS is_fl,
                   notional*coalesce(net_bp,0)/1e4 AS usd
            FROM lane_ledger
            WHERE lane_id='{LANE}' AND event='fill' AND ts >= '{CUT}'::timestamptz
              AND coalesce(meta_json->>'flatten','false')='true'
            ORDER BY usd ASC
        """)
        rows = cur.fetchall()

    print("=" * 100)
    print(f"止损是否真的兜住了？（当前时代出场腿 n={len(rows)}）")
    print("=" * 100)
    if not rows:
        print("  无数据")
        return 0

    nets = [float(r[3]) for r in rows]
    beyond = [r for r in rows if float(r[3]) < -STOP_CAP]
    print(f"  止损上限（disaster_stop cap_bp） = {STOP_CAP:.0f}bp")
    print(f"  出场腿净额分布: 中位 {st.median(nets):+.1f}bp  均值 {st.mean(nets):+.1f}bp")
    print(f"  **超过止损上限的腿 = {len(beyond)} / {len(rows)}"
          f" ({len(beyond)/len(rows)*100:.1f}%)**")
    if beyond:
        b_usd = sum(float(r[6]) for r in beyond)
        tot_usd = sum(float(r[6]) for r in rows)
        print(f"  这些腿合计 ${b_usd:+.2f}，占出场腿总亏损的 "
              f"{abs(b_usd)/max(abs(tot_usd),1e-9)*100:.1f}%")
        print()
        print(f"  {'ts':<14}{'sym':<9}{'notional':>10}{'net_bp':>9}{'usd':>10}  exit_path")
        for r in beyond[:10]:
            print(f"  {r[0].strftime('%m-%d %H:%M'):<14}{str(r[1])[:8]:<9}"
                  f"{float(r[2]):>10,.0f}{float(r[3]):>9.1f}{float(r[6]):>10.3f}"
                  f"  {r[4]}")

    print()
    print("=" * 100)
    print("分层：损失深度 vs 出现频率")
    print("=" * 100)
    for lo, hi, name in ((0, 15, "0~15bp"), (15, 40, "15~40bp"), (40, 60, "40~60bp"),
                         (60, 150, "**60~150bp**"), (150, 10 ** 9, "**>150bp**")):
        g = [float(r[3]) for r in rows if lo <= -float(r[3]) < hi or (lo == 0 and float(r[3]) >= 0 and hi == 15)]
        if lo == 0:
            g = [float(r[3]) for r in rows if float(r[3]) >= 0]
        if not g:
            continue
        usd = sum(float(r[6]) for r in rows if (float(r[3]) >= 0) if lo == 0 and float(r[3]) in g) if lo == 0 else 0.0
        print(f"  {name:<14} n={len(g):>4}  均值 {st.mean(g):+8.1f}bp")

    print()
    tot = sum(float(r[6]) for r in rows)
    deep = [r for r in rows if float(r[3]) < -150]
    d_usd = sum(float(r[6]) for r in deep)
    print(f"  >150bp 的深亏腿 {len(deep)} 条，合计 ${d_usd:+.2f}"
          f"（占出场腿总亏损 {abs(d_usd)/max(abs(tot),1e-9)*100:.0f}%）")
    print()
    if len(beyond) / max(len(rows), 1) > 0.10:
        print("  ⚠️ 止损**经常被穿透** ⇒ 风险模型按 stop_bp 定规模会**低估真实风险**，")
        print("     所以开闸放大腿量前必须先解释这些穿透。")
    else:
        print("  ⇒ 穿透比例低，止损基本有效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
