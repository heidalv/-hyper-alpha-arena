# -*- coding: utf-8 -*-
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"d:\001Alpha\Hyper-Alpha-Arena")
from backend.database.connection import AnalyticsSessionLocal
from sqlalchemy import text
db = AnalyticsSessionLocal()
print("== walk_forward_reports subject_type 分布 + run_at ==")
try:
    rows = db.execute(text("SELECT subject_type, count(*), min(run_at), max(run_at) FROM walk_forward_reports GROUP BY subject_type")).fetchall()
    for r in rows:
        print("  ", r)
except Exception as e:
    print("失败:", e); db.rollback()
print("== factor_evolution_log EVALUATE/PURGE/PROMOTE/DATA/ONLINE_WEIGHTS 全记录 ==")
try:
    rows = db.execute(text("SELECT id, created_at, phase, factor_id, source, action, reason FROM factor_evolution_log WHERE phase IN ('EVALUATE','PURGE','PROMOTE','DATA','ONLINE_WEIGHTS') ORDER BY id")).fetchall()
    for r in rows:
        print("  ", tuple(str(x)[:50] for x in r))
except Exception as e:
    print("失败:", e); db.rollback()
db.close()
