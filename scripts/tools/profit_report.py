# -*- coding: utf-8 -*-
r"""[h900 2026-10-07] 高频实战成绩单 —— 一眼看清"赚没赚钱、哪里在赚/亏"。

用法:  .venv\Scripts\python.exe scripts\tools\profit_report.py [小时数=6]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0

from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal

with system_identity():
    with SessionLocal() as db:
        print(f"========== 高频实战成绩单(近 {HOURS}h) ==========")
        r = db.execute(text(
            "SELECT COUNT(*) n, SUM(net_bp*notional)/10000.0 pnl,"
            " SUM(net_bp*notional)/NULLIF(SUM(notional),0) avg_bp,"
            " SUM(fee_bp*notional)/10000.0 fee "
            "FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
            " AND ts > NOW() - make_interval(hours => :h)"), {"h": int(HOURS)}).fetchone()
        n, pnl, avg_bp, fee = int(r[0] or 0), float(r[1] or 0), float(r[2] or 0), float(r[3] or 0)
        verdict = "✅ 盈利" if pnl > 0 else ("➖ 持平" if pnl > -1 else "❌ 亏损")
        print(f"总净盈亏: ${pnl:+.2f}   {verdict}")
        print(f"成交腿数: {n}   平均每腿: {avg_bp:+.2f}bp   手续费: ${fee:.2f}")
        if n:
            # 胜率
            r2 = db.execute(text(
                "SELECT COUNT(*) FILTER (WHERE net_bp>0)*100.0/COUNT(*) "
                "FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
                " AND ts > NOW() - make_interval(hours => :h)"), {"h": int(HOURS)}).fetchone()
            print(f"腿胜率: {float(r2[0] or 0):.0f}%")

        print("\n--- 逐小时净盈亏 ---")
        for h, nn, p in db.execute(text(
                "SELECT date_trunc('hour', ts) h, COUNT(*),"
                " SUM(net_bp*notional)/10000.0 FROM lane_ledger"
                " WHERE lane_id='mm_asterdex' AND event='fill'"
                " AND ts > NOW() - make_interval(hours => :h)"
                " GROUP BY 1 ORDER BY 1"), {"h": int(HOURS)}).fetchall():
            bar = "█" * int(min(20, abs(float(p or 0)) / 2)) if p else ""
            sign = "+" if float(p or 0) > 0 else "-"
            print(f"  {h.strftime('%H:%M')}  ${float(p or 0):+7.2f}  {sign}{bar}")

        print("\n--- 出场路径(最亏在前) ---")
        for path, nn, p in db.execute(text(
                "SELECT meta_json->>'exit_path', COUNT(*),"
                " SUM(net_bp*notional)/10000.0 FROM lane_ledger"
                " WHERE lane_id='mm_asterdex' AND event='fill'"
                " AND ts > NOW() - make_interval(hours => :h)"
                " AND meta_json->>'exit_path' IS NOT NULL"
                " GROUP BY 1 ORDER BY 3"), {"h": int(HOURS)}).fetchall():
            print(f"  {path:26} n={nn:4}  ${float(p or 0):+7.2f}")

        print("\n--- 逐币(最亏在前,只显示有成交的) ---")
        for s, nn, p in db.execute(text(
                "SELECT symbol, COUNT(*), SUM(net_bp*notional)/10000.0"
                " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
                " AND ts > NOW() - make_interval(hours => :h)"
                " GROUP BY 1 ORDER BY 3"), {"h": int(HOURS)}).fetchall():
            print(f"  {s:12} n={nn:4}  ${float(p or 0):+7.2f}")
