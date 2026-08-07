# -*- coding: utf-8 -*-
import os, sys, sqlite3
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

print("### HERMES SQLite schema ###")
con = sqlite3.connect(os.path.join(ROOT, "data", "hermes_evolution.db"))
cur = con.cursor()
for t in ['prompt_versions','prompt_ab_tests','proposal_wisdom_records','param_effect_patterns','strategy_genesis_candidates','task_run_log','agent_decision_wisdom']:
    cur.execute(f"PRAGMA table_info({t})")
    cols = [r[1] for r in cur.fetchall()]
    print(f"\n{t}:\n  {cols}")
con.close()

print("\n\n### Postgres schema ###")
from sqlalchemy import create_engine, text
for db in ["alpha_arena", "alpha_analytics"]:
    eng = create_engine(f"postgresql+psycopg://laobao:alpha_pass@localhost:5432/{db}", isolation_level="AUTOCOMMIT")
    tabs = ['strategy_memories','strategy_trades','strategy_templates','prompt_training_records','strategy_regime_scores','drl_performance','coordinator_actions','decision_retrospectives','mlto_signal_weights','mlto_thesis_events']
    with eng.connect() as c:
        for t in tabs:
            try:
                r = c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name=:t ORDER BY ordinal_position"), {"t": t})
                cols = [row[0] for row in r.fetchall()]
                if cols:
                    print(f"\n[{db}] {t}:\n  {cols}")
            except Exception as e:
                print(f"[{db}] {t} ERR {str(e)[:80]}")
