# -*- coding: utf-8 -*-
"""Z155 (目标项 (c)): paper/live 两态当前实际配置 + 会话分布（口径核验）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

KEYS = [
    "TRADING_MODE",
    "PAPER_FAST_TRIAL",
    "MIDLONG_EXEC_AUTHORITY",
    "MIDLONG_MLTO_CONTROLS_EXEC",
    "MIDLONG_LONG_MODE",
    "TREND_E1_LIVE_ASTER",
    "LIVE_TPSL_SYNC",
    "MIDLONG_MAX_OPEN_POSITIONS",
]
print("=== 关键 mode 相关 env 实际生效值 ===")
for k in KEYS:
    print(f"  {k:32s} = {os.getenv(k)!r}")

print("\n=== full_auto_sessions 实际分布 ===")
try:
    from sqlalchemy import create_engine, text

    url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if url and url.startswith("postgresql+psycopg://"):
        url = url.replace("postgresql+psycopg://", "postgresql+psycopg2://")
    eng = create_engine(url)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        try:
            rows = c.execute(text(
                "select trading_mode, status, count(*) from full_auto_sessions "
                "group by 1,2 order by 3 desc"
            )).fetchall()
            for r in rows:
                print(f"  trading_mode={r[0]!r:10s} status={r[1]!r:12s} 会话数={r[2]}")
        except Exception as e:  # noqa: BLE001
            c.rollback()
            print("  session 查询失败:", str(e)[:160])
        try:
            rows = c.execute(text(
                "select account_type, count(*) from accounts group by 1 order by 2 desc"
            )).fetchall()
            print("  accounts:")
            for r in rows:
                print(f"    {r[0]!r}: {r[1]}")
        except Exception as e:  # noqa: BLE001
            c.rollback()
            print("  accounts 查询失败:", str(e)[:160])
except Exception as e:  # noqa: BLE001
    print("  DB 失败:", str(e)[:200])
