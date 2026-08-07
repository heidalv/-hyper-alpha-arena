# -*- coding: utf-8 -*-
"""临时诊断 v3：检查最新 thesis 更新与 maintain 执行"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from sqlalchemy import text, create_engine

def get_dsn(env):
    with open(r"D:\001Alpha\Hyper-Alpha-Arena\.env", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith(env + "="):
                return line.split("=", 1)[1].strip()
    return None

ae = create_engine(get_dsn("ANALYTICS_DATABASE_URL"))

def q(label, eng, sql, params=None):
    print(f"\n===== {label} =====")
    try:
        with eng.connect() as db:
            db.execute(text("SET app.is_admin='on'"))
            rows = db.execute(text(sql), params or {}).fetchall()
            if not rows:
                print("  (空)")
            for r in rows:
                print("  ", dict(r._mapping))
    except Exception as e:
        print(f"  ERROR: {e}")

q("mlto_thesis 精确状态", ae, """
    SELECT thesis_id, session_id, symbol, tier, direction, llm_conviction,
           open_readiness, tranche_stage, updated_at, created_at
    FROM mlto_thesis WHERE session_id='fa_10d44c724e' ORDER BY symbol
""")

q("mlto_thesis_events 今天21点后", ae, """
    SELECT id, thesis_id, event_type, ts FROM mlto_thesis_events
    WHERE ts >= '2026-08-04 21:00:00' ORDER BY ts DESC LIMIT 30
""")

q("llm_usage_logs 今天21点后", ae, """
    SELECT id, call_type, success, model, total_tokens, created_at FROM llm_usage_logs
    WHERE created_at >= '2026-08-04 21:00:00' ORDER BY id DESC LIMIT 40
""")

q("llm_usage_logs 21点后 call_type 分布", ae, """
    SELECT call_type, success, COUNT(*) cnt FROM llm_usage_logs
    WHERE created_at >= '2026-08-04 21:00:00' GROUP BY call_type, success ORDER BY cnt DESC
""")
