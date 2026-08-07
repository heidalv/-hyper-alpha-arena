# -*- coding: utf-8 -*-
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"d:\001Alpha\Hyper-Alpha-Arena")
from backend.database.connection import AnalyticsSessionLocal
from sqlalchemy import text
db = AnalyticsSessionLocal()
print("== walk_forward_reports ==")
try:
    n = db.execute(text("SELECT count(*) FROM walk_forward_reports")).scalar()
    print("行数:", n)
    rows = db.execute(text("SELECT subject_type, subject_id, pbo, dsr, passed, created_at FROM walk_forward_reports ORDER BY id DESC LIMIT 8")).fetchall()
    for r in rows:
        print("  ", tuple(str(x)[:38] for x in r))
except Exception as e:
    print("查询失败:", e)
print("== factor_evolution_log 今日(2026-08-06) phase 分布 ==")
try:
    rows = db.execute(text("SELECT phase, count(*) FROM factor_evolution_log WHERE created_at::date = '2026-08-06' GROUP BY phase")).fetchall()
    for r in rows:
        print("  ", r)
except Exception as e:
    print("查询失败:", e)
print("== factor_evolution_log 08-05 之后按天分布 ==")
try:
    rows = db.execute(text("SELECT created_at::date AS d, phase, count(*) FROM factor_evolution_log WHERE created_at >= '2026-08-04' GROUP BY d, phase ORDER BY d DESC, phase")).fetchall()
    for r in rows:
        print("  ", r)
except Exception as e:
    print("查询失败:", e)
db.close()
