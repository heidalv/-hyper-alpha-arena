# -*- coding: utf-8 -*-
"""手续费面板又被重置的排查：stats_since 现值 + 谁写的 + 账户重置时间。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
print("stats_since      =", m.get("stats_since"))
print("account_reset_at =", m.get("account_reset_at"))
print("h443_trial.started_at =", (m.get("h443_trial") or {}).get("started_at"))
print("\n近 8 条 ops（看谁改了统计时代）：")
for e in (m.get("ops_changes") or [])[-8:]:
    f = e.get("field") or e.get("action")
    print(f"  {str(e.get('ts'))[:19]} {str(f)[:46]:<46} {str(e.get('note'))[:60]}")

print("\n按 stats_since 裁剪的手续费（面板口径）：")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(fee_bp/1e4*notional)::numeric,4)
    FROM lane_ledger WHERE event='fill' AND ts >= %s::timestamptz
""", (m.get("stats_since"),))
r = cur.fetchone()
print(f"  {r[0]} 腿，手续费 ${r[1]}")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(fee_bp/1e4*notional)::numeric,4)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '24 hours'
""")
r = cur.fetchone()
print(f"（近 24h 实际：{r[0]} 腿，手续费 ${r[1]}）")
