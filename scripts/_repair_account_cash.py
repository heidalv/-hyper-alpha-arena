# -*- coding: utf-8 -*-
"""[h675b 2026-10-01] 修"账户余额未修":收益(realized)已对齐账本,但
available_balance 仍含被删幽灵腿的 +24.4476(后端把它当"外部划拨")。

正确余额 = 基准(shadow_equity=300) + 时代内净(Σpaper_pnl + Σpaper_fee)
         = 300 + (−14.4079) + (−0.8954) = 284.6967

同时修 account 行与 asterdex 分所行(服务用分所行做增量续算 ⇒ 只改一处会被拉回)。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LANE = "mm_asterdex"
VENUE = "asterdex"


def main() -> int:
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from backend.services import lane_registry as reg

    meta = (reg.get_lane(LANE) or {}).get("meta") or {}
    aid = meta.get("paper_account_id")
    base = float(meta.get("shadow_equity") or 300.0)
    epoch = str(meta.get("account_reset_at") or "") or None
    with system_identity():
        with SessionLocal() as db:
            if epoch:
                pnl = db.execute(text(
                    "SELECT COALESCE(SUM(amount_usd),0) FROM arbitrage_paper_ledgers"
                    " WHERE account_id=:a AND action='paper_pnl' AND created_at >= :e"),
                    {"a": aid, "e": epoch}).fetchone()[0]
                fee = db.execute(text(
                    "SELECT COALESCE(SUM(amount_usd),0) FROM arbitrage_paper_ledgers"
                    " WHERE account_id=:a AND action='paper_fee' AND created_at >= :e"),
                    {"a": aid, "e": epoch}).fetchone()[0]
            else:
                pnl = fee = 0.0
            cash = round(base + float(pnl) + float(fee), 4)
            r1 = db.execute(text(
                "UPDATE arbitrage_paper_accounts SET"
                " available_balance=:c, frozen_balance=0, realized_pnl=:p,"
                " updated_at=now() WHERE id=:a"),
                {"c": cash, "p": round(float(pnl), 4), "a": aid})
            r2 = db.execute(text(
                "UPDATE arbitrage_paper_exchange_balances SET"
                " available_usd=:c, frozen_usd=0, allocated_usd=:b"
                " WHERE account_id=:a AND exchange=:v"),
                {"c": cash, "b": base, "a": aid, "v": VENUE})
            db.commit()
            print(f"时代起点 {epoch}")
            print(f"时代内 net = {float(pnl):+.4f}(pnl) + {float(fee):+.4f}(fee)"
                  f" = {float(pnl)+float(fee):+.4f}")
            print(f"正确现金 = 基准 {base} + 净 = {cash}")
            print(f"更新: account 行 {r1.rowcount} 条 · {VENUE} 分所行 {r2.rowcount} 条")
            row = db.execute(text(
                "SELECT available_balance, realized_pnl FROM"
                " arbitrage_paper_accounts WHERE id=:a"), {"a": aid}).fetchone()
            print(f"复核: available={row[0]} realized={row[1]}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
