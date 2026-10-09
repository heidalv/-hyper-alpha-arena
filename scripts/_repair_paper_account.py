# -*- coding: utf-8 -*-
"""[h675 2026-10-01] 统一模拟账户行与 append-only 账本对账修复。

背景:今日删两笔幽灵腿(lane_ledger + arbitrage_paper_ledgers),但
`arbitrage_paper_accounts` 行里已累计的数值没有回退 ⇒ 前端显示 +10.04U 假盈利。

口径(必须与车道账本=事实源一致):
  equity = 基准(shadow_equity) + Σpaper_pnl(时代内) + Σpaper_fee(时代内)
  时代起点 = meta.account_reset_at(终身流水含 9-14 起的旧时代,混用会算错)
  **不做 position_id 去重**:账本历史上 position_id 有重复(H76 硬编码 mm:{symbol}),
  去重会把 2651 条 pnl 塌成 15 个仓位 ⇒ 严重低估。实测时代内全额求和与车道账本
  差 0.0096U(2737 腿 −15.3220 vs 2651 条 −15.3316)⇒ 全额求和才自洽。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"


def main() -> int:
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from backend.services import lane_registry as reg

    lane = reg.get_lane(LANE) or {}
    meta = lane.get("meta") or {}
    aid = meta.get("paper_account_id")
    if not aid:
        print("x 车道无 paper_account_id")
        return 1
    base = float(meta.get("shadow_equity") or 300.0)
    epoch = str(meta.get("account_reset_at") or "") or None

    with system_identity():
        with SessionLocal() as db:
            if epoch:
                pnl = db.execute(text(
                    "SELECT COALESCE(SUM(amount_usd),0), count(*) FROM"
                    " arbitrage_paper_ledgers WHERE account_id=:a"
                    " AND action='paper_pnl' AND created_at >= :e"),
                    {"a": aid, "e": epoch}).fetchone()
                fee = db.execute(text(
                    "SELECT COALESCE(SUM(amount_usd),0), count(*) FROM"
                    " arbitrage_paper_ledgers WHERE account_id=:a"
                    " AND action='paper_fee' AND created_at >= :e"),
                    {"a": aid, "e": epoch}).fetchone()
            else:
                pnl = db.execute(text(
                    "SELECT COALESCE(SUM(amount_usd),0), count(*) FROM"
                    " arbitrage_paper_ledgers WHERE account_id=:a"
                    " AND action='paper_pnl'"), {"a": aid}).fetchone()
                fee = db.execute(text(
                    "SELECT COALESCE(SUM(amount_usd),0), count(*) FROM"
                    " arbitrage_paper_ledgers WHERE account_id=:a"
                    " AND action='paper_fee'"), {"a": aid}).fetchone()
            realized = round(float(pnl[0] or 0.0), 4)
            fee_sum = round(float(fee[0] or 0.0), 4)
            equity = round(base + realized + fee_sum, 4)
            r = db.execute(text(
                "UPDATE arbitrage_paper_accounts SET"
                " total_equity=:e, available_balance=:e, frozen_balance=0,"
                " realized_pnl=:r, updated_at=now() WHERE id=:a"),
                {"e": equity, "r": realized, "a": aid})
            db.commit()
            print(f"时代起点 account_reset_at = {epoch}")
            print(f"account_id={aid} 行数更新={r.rowcount}")
            print(f"时代内: pnl {realized:+.4f}U({pnl[1]} 条)"
                  f" + fee {fee_sum:+.4f}U({fee[1]} 条)")
            print(f"重算: 基准 {base} + {realized:+.4f} + {fee_sum:+.4f}"
                  f" = 权益 {equity:+.4f}")
            row = db.execute(text(
                "SELECT total_equity, available_balance, realized_pnl FROM"
                " arbitrage_paper_accounts WHERE id=:a"), {"a": aid}).fetchone()
            print(f"复核: equity={row[0]} available={row[1]} realized={row[2]}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
