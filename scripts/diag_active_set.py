# -*- coding: utf-8 -*-
"""FactorActiveSet 活跃集真实状态 + 质量分布。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

from backend.database.connection import AnalyticsSessionLocal
from sqlalchemy import text

db = AnalyticsSessionLocal()
try:
    print("=== factor_active_set 按 state 分布 ===")
    for r in db.execute(text(
        "SELECT state, COUNT(*) FROM factor_active_set GROUP BY state ORDER BY 2 DESC"
    )):
        print("  ", r)

    print("\n=== TRADABLE（PAPER/SMALL_LIVE/ACTIVE）质量分布 ===")
    for r in db.execute(text(
        "SELECT state, COUNT(*), "
        "ROUND(AVG(COALESCE(icir,0))::numeric,4) AS avg_icir, "
        "SUM(CASE WHEN COALESCE(icir,0) > 0.2 THEN 1 ELSE 0 END) AS strong, "
        "SUM(CASE WHEN COALESCE(icir,0) <= 0 THEN 1 ELSE 0 END) AS dead_icir "
        "FROM factor_active_set WHERE state IN ('PAPER','SMALL_LIVE','ACTIVE') "
        "GROUP BY state"
    )):
        print("  ", r)

    print("\n=== TRADABLE 前 20（按 |icir| 排序）===")
    for r in db.execute(text(
        "SELECT factor_id, state, ROUND(COALESCE(icir,0)::numeric,4) AS icir, "
        "ROUND(COALESCE(incremental_corr,0)::numeric,4) AS incr_corr, "
        "COALESCE(period,'') AS period, "
        "to_char(COALESCE(last_evaluated_at, activated_at), 'MM-DD HH24:MI') AS last_eval "
        "FROM factor_active_set WHERE state IN ('PAPER','SMALL_LIVE','ACTIVE') "
        "ORDER BY abs(COALESCE(icir,0)) DESC LIMIT 20"
    )):
        print("  ", r)

    print("\n=== 最近评估时间分布（活性/新鲜度）===")
    for r in db.execute(text(
        "SELECT CASE WHEN last_evaluated_at > NOW() - INTERVAL '1 day' THEN '24h内' "
        "            WHEN last_evaluated_at > NOW() - INTERVAL '7 days' THEN '7天内' "
        "            ELSE '>7天' END AS fresh, COUNT(*) "
        "FROM factor_active_set WHERE state IN ('PAPER','SMALL_LIVE','ACTIVE') "
        "GROUP BY 1 ORDER BY 1"
    )):
        print("  ", r)
finally:
    db.close()
print("DONE")
