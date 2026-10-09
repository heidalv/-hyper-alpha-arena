# -*- coding: utf-8 -*-
"""[h760] 检查 DC 快照名单来源(system_configs / running 会话)。"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.database.connection import SessionLocal

with SessionLocal() as db:
    for k in ("hyperliquid_selected_symbols", "user_trading_pairs"):
        r = db.execute(text("SELECT value FROM system_configs WHERE key=:k"), {"k": k}).first()
        v = str(r[0]) if r else "(无)"
        print(f"  {k} = {v[:400]}")
    rows = db.execute(text(
        "SELECT session_id, symbols, auto_coin_symbols FROM full_auto_sessions"
        " WHERE status='running' ORDER BY started_at DESC LIMIT 3")).mappings().all()
    print(f"  running 会话 {len(rows)} 个:")
    for r in rows:
        print(f"    {r['session_id']}: symbols={str(r['symbols'])[:200]} | auto={str(r['auto_coin_symbols'])[:120]}")
