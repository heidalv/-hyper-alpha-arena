# -*- coding: utf-8 -*-
"""[交易分析 R6] 把日度交易与链路事件对齐（目标③：用已查清的链路结论解释成因）。

链路事件（本会话已确证）：
  A 学习冻结：mlto_signal_weights 停在 2026-09-18 12:40 → 2026-09-24 20:27（首笔真实回写）
  B 负边际源停用：SourceTrust 于 2026-09-24 11:45:30 停用 dual:event_impact / dual:trend_chart_review
  C 缩仓：名义上限 0.35→0.15（生效日从数据推断：日均名义）
  D OWM shadow：2026-09-25 01:07 起长线信心不再被砍半
  E 采集：周期对齐/预载等（09-24 13:51 起）
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

PNL = """
  (CASE WHEN lower(side) IN ('long','buy') THEN (close_price - entry_price)
        ELSE (entry_price - close_price) END) * size
"""
NET = f"(({PNL}) - coalesce(partial_fee_paid,0) - coalesce(final_fee_paid,0))"
HOLD_H = "EXTRACT(EPOCH FROM (closed_at - opened_at))/3600.0"

with SessionLocal() as s:
    print("=== 日度表：交易 × 链路时间线（09-15 起）===")
    print(f"  {'日期':11s} {'笔':>3s} {'净':>8s} {'<1h笔':>5s} {'<1h净':>8s} {'均名义':>8s} {'mid笔':>5s} {'long笔':>6s}")
    for x in s.execute(text(f"""
        SELECT date_trunc('day', closed_at)::date d,
               count(*) n, sum({NET}) net,
               count(*) FILTER (WHERE {HOLD_H} < 1) n1,
               coalesce(sum({NET}) FILTER (WHERE {HOLD_H} < 1), 0) net1,
               avg(size * entry_price) notional,
               count(*) FILTER (WHERE timeframe_tier='mid') nm,
               count(*) FILTER (WHERE timeframe_tier='long') nl
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY 1
    """)):
        print(f"  {str(x[0]):11s} {x[1]:3d} {float(x[2]):8.2f} {x[3]:5d} {float(x[4]):8.2f} "
              f"{float(x[5]):8.1f} {x[6]:5d} {x[7]:6d}")

    print("\n=== 缩仓生效日推断：按日 中位名义 ===")
    for x in s.execute(text("""
        SELECT date_trunc('day', opened_at)::date d,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY size*entry_price) med_notional,
               count(*) n
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY 1
    """)):
        print(f"  {x[0]}  中位名义={float(x[1]):8.1f}  n={x[2]}")

    print("\n=== 对照：<1h 桶 在关键事件前后的分布 ===")
    for label, cond in (
        ("学习冻结期(09-18 12:40~09-24 20:27)", "closed_at BETWEEN '2026-09-18 12:40' AND '2026-09-24 20:27'"),
        ("负边际源停用后(>=09-24 11:45)", "closed_at >= '2026-09-24 11:45'"),
        ("OWM shadow 后(>=09-25 01:07)", "closed_at >= '2026-09-25 01:07'"),
    ):
        x = s.execute(text(f"""
            SELECT count(*) FILTER (WHERE {HOLD_H} < 1) n1,
                   coalesce(sum({NET}) FILTER (WHERE {HOLD_H} < 1),0) net1,
                   count(*) nall,
                   coalesce(sum({NET}),0) netall,
                   count(*) FILTER (WHERE {HOLD_H} < 1)::float / greatest(count(*),1) frac
            FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15' AND {cond}
        """)).first()
        print(f"  {label:38s} <1h {x[0]}/{x[2]} ({float(x[4])*100:.0f}%) 净={float(x[1]):8.2f} | 全窗口净={float(x[3]):8.2f}")

    print("\n=== mid 车道 小亏退出（min_roi_decay）按周 ===")
    for x in s.execute(text(f"""
        SELECT date_trunc('week', closed_at)::date w, count(*) n, round(sum({NET})::numeric,2) net
        FROM paper_positions
        WHERE status='closed' AND closed_at >= '2026-09-15' AND close_reason LIKE 'exit_policy:min_roi_decay%'
        GROUP BY 1 ORDER BY 1
    """)):
        print(f"  {x[0]}  n={x[1]:2d} 净={x[2]}")
