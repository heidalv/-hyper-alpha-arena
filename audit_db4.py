# -*- coding: utf-8 -*-
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"d:\001Alpha\Hyper-Alpha-Arena")
from backend.database.connection import AnalyticsSessionLocal
from sqlalchemy import text
db = AnalyticsSessionLocal()
print("== walk_forward_reports 列 ==")
try:
    rows = db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='walk_forward_reports' ORDER BY ordinal_position")).fetchall()
    print("  ", [r[0] for r in rows])
except Exception as e:
    print("失败:", e); db.rollback()
print("== walk_forward_reports 最近 8 行 ==")
try:
    rows = db.execute(text("SELECT subject_type, subject_id, pbo, dsr, passed FROM walk_forward_reports ORDER BY id DESC LIMIT 8")).fetchall()
    for r in rows:
        print("  ", tuple(str(x)[:36] for x in r))
except Exception as e:
    print("失败:", e); db.rollback()
print("== factor_evolution_log 今日(2026-08-06) phase 分布 ==")
try:
    rows = db.execute(text("SELECT phase, count(*) FROM factor_evolution_log WHERE created_at::date = '2026-08-06' GROUP BY phase")).fetchall()
    for r in rows:
        print("  ", r)
except Exception as e:
    print("失败:", e); db.rollback()
print("== factor_evolution_log 08-04 后按天/phase ==")
try:
    rows = db.execute(text("SELECT created_at::date AS d, phase, count(*) FROM factor_evolution_log WHERE created_at >= '2026-08-04' GROUP BY d, phase ORDER BY d DESC, phase")).fetchall()
    for r in rows:
        print("  ", r)
except Exception as e:
    print("失败:", e); db.rollback()
print("== factor_evolution_log phase=EVALUATE/PURGE/PROMOTE 记录 ==")
try:
    rows = db.execute(text("SELECT id, created_at, phase, factor_id, source, action, reason FROM factor_evolution_log WHERE phase IN ('EVALUATE','PURGE','PROMOTE','review','degrade') ORDER BY id DESC LIMIT 15")).fetchall()
    for r in rows:
        print("  ", tuple(str(x)[:42] for x in r))
except Exception as e:
    print("失败:", e); db.rollback()
db.close()
