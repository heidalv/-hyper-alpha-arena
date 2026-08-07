# -*- coding: utf-8 -*-
"""一次性 DB 只读核对：card 落库现状 / phase 分布 / active factor / slimming 报告"""
import os, sys, io, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = r"d:\001Alpha\Hyper-Alpha-Arena"
sys.path.insert(0, BASE)

print("== factor_evolution_log phase 分布（含时间） ==")
try:
    from backend.database.models import FactorEvolutionLog, FactorActiveSet
    from backend.database.connection import AnalyticsSessionLocal
    db = AnalyticsSessionLocal()
    from sqlalchemy import func, text
    rows = db.query(FactorEvolutionLog.phase, func.count()).group_by(FactorEvolutionLog.phase).all()
    for p, c in rows:
        print(f"  phase={p}: {c}")
    print("  总条数:", db.query(FactorEvolutionLog).count())
    # 最近 10 条
    print("-- 最近 12 条记录 --")
    for r in db.query(FactorEvolutionLog).order_by(FactorEvolutionLog.id.desc()).limit(12).all():
        ts = getattr(r, "created_at", None) or getattr(r, "timestamp", None) or ""
        print(f"  id={r.id} {ts} phase={r.phase} factor={str(r.factor_id)[:28]} src={r.source} action={r.action}")
    # 是否有 card 记录及 metrics 里有没有 card key
    card_rows = db.query(FactorEvolutionLog).filter(FactorEvolutionLog.phase == "card").all()
    print("card 记录数:", len(card_rows))
    for r in card_rows[:3]:
        m = r.metrics or {}
        print("  card factor=", r.factor_id[:30], "metrics keys=", list(m.keys())[:6], "有card键=", "card" in m)
    db.close()
except Exception as e:
    import traceback; traceback.print_exc()

print("")
print("== factor_active_set 状态分布 ==")
try:
    from backend.database.connection import AnalyticsSessionLocal
    db = AnalyticsSessionLocal()
    rows = db.query(FactorActiveSet.state, func.count()).group_by(FactorActiveSet.state).all()
    for s, c in rows:
        print(f"  state={s}: {c}")
    for r in db.query(FactorActiveSet).filter(FactorActiveSet.state == "ACTIVE").all():
        print("  ACTIVE:", r.factor_id, "icir=", r.icir, "activated_at=", r.activated_at)
    db.close()
except Exception as e:
    import traceback; traceback.print_exc()

print("")
print("== wfo_reports 记录数（WFO 是否落库） ==")
try:
    from backend.database.connection import AnalyticsSessionLocal
    db = AnalyticsSessionLocal()
    from sqlalchemy import text
    try:
        n = db.execute(text("SELECT count(*) FROM wfo_reports")).scalar()
        print("  wfo_reports 行数:", n)
        rows = db.execute(text("SELECT id, factor_id, pbo, created_at FROM wfo_reports ORDER BY id DESC LIMIT 5")).fetchall()
        for r in rows:
            print("  ", r)
    except Exception as e:
        print("  wfo_reports 查询失败:", e)
    db.close()
except Exception as e:
    import traceback; traceback.print_exc()

print("")
print("== factor_slimming_audit 报告文件 ==")
try:
    import glob
    for f in sorted(glob.glob(os.path.join(BASE, "logs", "*slim*")) + glob.glob(os.path.join(BASE, "reports", "*slim*")) + glob.glob(os.path.join(BASE, "backend", "reports", "*slim*"))):
        print("  ", f, os.path.getsize(f), "bytes")
except Exception as e:
    print("  ", e)
