# -*- coding: utf-8 -*-
"""隔离测试：复现 API 里的账户总账查询，看真实异常。"""
import sys
import pathlib
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from sqlalchemy import text as sa_text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    print("imports OK")
    with system_identity():
        with SessionLocal() as db:
            row = db.execute(sa_text(
                "SELECT"
                "  COALESCE(SUM(amount_usd) FILTER (WHERE action='paper_pnl'),0) AS realized_all,"
                "  COALESCE(SUM(amount_usd) FILTER (WHERE action='paper_fee'),0) AS fee_all,"
                "  COALESCE(SUM(amount_usd) FILTER (WHERE action='create_account'),0) AS initial,"
                "  COUNT(*) FILTER (WHERE action='paper_pnl') AS n_pnl"
                "  FROM arbitrage_paper_ledgers WHERE account_id = :a"), {"a": 101}).first()
    print("row:", row)
except Exception:
    traceback.print_exc()

# 再查 lane meta 里的 account id
try:
    from backend.services import lane_registry
    m = (lane_registry.get_lane("mm_asterdex") or {}).get("meta") or {}
    print("paper_account_id =", m.get("paper_account_id"))
except Exception:
    traceback.print_exc()
