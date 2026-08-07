# -*- coding: utf-8 -*-
"""DB 只读查询：factor_active_set / factor_evolution_log"""
import os, sys, io, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = r"d:\001Alpha\Hyper-Alpha-Arena"
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "backend"))

try:
    from backend.database.connection import AnalyticsSessionLocal
except Exception as e:
    print("import connection 失败:", e)
    try:
        from database.connection import AnalyticsSessionLocal
    except Exception as e2:
        print("import 失败2:", e2)
        sys.exit(1)

db = AnalyticsSessionLocal()
from sqlalchemy import text

print("1) factor_active_set 状态分布:")
rows = db.execute(text("SELECT state, COUNT(*) FROM factor_active_set GROUP BY state ORDER BY 2 DESC")).fetchall()
for r in rows:
    print("   ", r[0], ":", r[1])

print("")
print("2) factor_active_set 总行数 / 最近激活时间:")
rows = db.execute(text("SELECT COUNT(*) FROM factor_active_set")).fetchall()
print("   总行数:", rows[0][0])
try:
    rows = db.execute(text("SELECT MAX(activated_at) FROM factor_active_set")).fetchall()
    print("   MAX(activated_at):", rows[0][0])
except Exception as e:
    print("   activated_at 查询失败:", e)

print("")
print("3) factor_evolution_log phase 分布:")
rows = db.execute(text("SELECT phase, COUNT(*) FROM factor_evolution_log GROUP BY phase ORDER BY 2 DESC")).fetchall()
for r in rows:
    print("   ", r[0], ":", r[1])

print("")
print("4) factor_evolution_log 最近 created_at:")
rows = db.execute(text("SELECT MAX(created_at) FROM factor_evolution_log")).fetchall()
print("   ", rows[0][0])

print("")
print("5) phase=card 报告卡落库数:")
rows = db.execute(text("SELECT COUNT(*) FROM factor_evolution_log WHERE phase='card'")).fetchall()
print("   ", rows[0][0])

print("")
print("6) 挖掘/报告卡 source 分布:")
try:
    rows = db.execute(text("SELECT source, COUNT(*) FROM factor_evolution_log WHERE phase IN ('mine','card') GROUP BY source ORDER BY 2 DESC LIMIT 15")).fetchall()
    for r in rows:
        print("   ", r[0], ":", r[1])
except Exception as e:
    print("   source 查询失败:", e)

print("")
print("7) 最近 15 条 factor_evolution_log (phase, source, state_from->state_to, reason):")
rows = db.execute(text("SELECT created_at, phase, source, state_from, state_to, reason FROM factor_evolution_log ORDER BY id DESC LIMIT 15")).fetchall()
for r in rows:
    print("   ", r[0], "|", r[1], "|", r[2], "|", r[3], "->", r[4], "|", (r[5] or "")[:80])

print("")
print("8) factor_evolution_log 里 metrics 含 card 的行数:")
rows = db.execute(text("SELECT COUNT(*) FROM factor_evolution_log WHERE metrics LIKE '%card%'")).fetchall()
print("   ", rows[0][0])

print("")
print("9) metrics 含 wfo 的行:")
try:
    rows = db.execute(text("SELECT COUNT(*) FROM factor_evolution_log WHERE metrics LIKE '%oos_ic%' OR action LIKE '%wfo%'")).fetchall()
    print("   oos_ic/wfo 行数:", rows[0][0])
except Exception as e:
    print("   查询失败:", e)

print("")
print("10) factor_evolution_log 表结构:")
rows = db.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='factor_evolution_log' ORDER BY ordinal_position")).fetchall()
for r in rows:
    print("   ", r[0], r[1])
db.close()
