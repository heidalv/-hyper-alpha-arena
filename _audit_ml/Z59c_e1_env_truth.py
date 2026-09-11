# -*- coding: utf-8 -*-
"""Z59c: 补 load_dotenv 后的 E1 车道实况（Z59 漏读 .env，需纠正证据）。"""
from __future__ import annotations
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import backend.services.trend_e1_engine as E
import backend.services.trend_e1_f4_gate as G
from sqlalchemy import create_engine, text

print("=== 纠正后的 E1 配置实况（已 load_dotenv）===")
print("  TREND_E1_ENABLED          =", os.getenv("TREND_E1_ENABLED"))
print("  TREND_E1_ACCOUNT_IDS      =", os.getenv("TREND_E1_ACCOUNT_IDS"))
print("  TREND_E1_DRY_RUN          =", os.getenv("TREND_E1_DRY_RUN"))
print("  TREND_E1_LIVE_ASTER       =", os.getenv("TREND_E1_LIVE_ASTER"))
print("  e1_enabled()              =", E.e1_enabled())
print("  dry_run()                 =", E.dry_run())
print("  e1_account_ids()          =", E.e1_account_ids())
print("  long_lane_exclusive()     =", E.long_lane_exclusive())
print("  live_aster_requested()    =", G.live_aster_requested())
f4 = G.evaluate_f4_gate(persist=False)
print("  f4.passed / live_allowed  =", f4.get("passed"), "/", f4.get("live_allowed"))
print("  f4 失败项                 =", [c.get("name") for c in (f4.get("checks") or []) if not c.get("ok")])

print()
print("=== 真实数据：E1 是否曾有真单 ===")
URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
eng = create_engine(URL)
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for q, label in [
        ("select count(*) from strategy_trades where strategy_id like 'trend_e1%'", "strategy_trades trend_e1"),
        ("select count(*) from paper_positions where tenant_id is not null and timeframe_tier='long'", "tier=long 仓位"),
        ("select count(*) from paper_positions where timeframe_tier='long' and (entry_source='trend_e1' or position_metadata::text like '%trend_e1%')", "E1 来源 long 仓位"),
    ]:
        try:
            print(f"  {label}:", c.execute(text(q)).scalar())
        except Exception as e:
            c.rollback(); print(f"  {label}: err {str(e)[:80]}")
    try:
        cols = c.execute(text("select column_name from information_schema.columns where table_name='paper_positions' order by ordinal_position")).fetchall()
        print("  paper_positions 关键列:", [x[0] for x in cols if 'source' in x[0] or 'strategy' in x[0] or 'tier' in x[0] or 'nature' in x[0] or 'entry' in x[0]][:12])
    except Exception as e:
        c.rollback(); print("  cols err", str(e)[:80])
