"""重置车道绑定的模拟账户到 $300（arbitrage_paper_accounts 表口径）+ 统计时代重设。

[F218 2026-09-15] 为什么不是走 paper 引擎 API
--------------------------------------------------
车道权益的真实来源（runner.py:1053-1059）：
    equity = arbitrage_paper_accounts(id=paper_account_id).total_equity
             + COALESCE(realized_pnl, 0)
而 `POST /api/paper/reset/{id}` 属于**另一个** paper 引擎，其表里根本没有 101 号账户
（实测 404 "Paper account 101 not found" ✗）⇒ 必须直接改 `arbitrage_paper_accounts` ✓。
本脚本：
  ① 读取并打印重置前的行（留痕 ✓）；
  ② total_equity=300、realized_pnl=0、available_balance=300（其余金额列一并归零 ✓）；
  ③ meta.shadow_equity=300 + meta.stats_since=now（新统计时代 ✓），写入 ops_changes 审计 ✓；
  ④ **不动 lane_ledger**（账本不可篡改 ✓ —— 旧亏损保留，仅统计口径重设 ✓）。
用户已明确授权"重置模拟账户，初始资金还是 300" ✓。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

MONEY_COLS = ("total_equity", "available_balance", "frozen_balance", "realized_pnl",
              "unrealized_pnl", "floating_pnl", "used_margin")


def main() -> int:
    lane_id = sys.argv[1] if len(sys.argv) > 1 else "mm_asterdex"
    acct = int(sys.argv[2]) if len(sys.argv) > 2 else 101
    bal = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0

    with SessionLocal() as db:
        db.execute(text("SET statement_timeout = 30000"))
        cols = [r[0] for r in db.execute(text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='arbitrage_paper_accounts'")).fetchall()]
        row = db.execute(text(
            "SELECT * FROM arbitrage_paper_accounts WHERE id=:i"), {"i": acct}).first()
        if row is None:
            print(f"✗ arbitrage_paper_accounts 里没有 id={acct}（车道绑定的账户不存在）")
            return 2
        before = {c: getattr(row, c) for c in cols}
        print("重置前: " + str(before))
        sets = []
        for c in MONEY_COLS:
            if c in cols and c != "total_equity" and c != "available_balance":
                sets.append(f"{c}=:v_{c}")
        sets.append("total_equity=:bal")
        sets.append("available_balance=:bal")     # 可用资金也应=300（前端"可用"读数）
        db.execute(text("UPDATE arbitrage_paper_accounts SET " + ", ".join(sets)
                        + " WHERE id=:i"),
                   {f"v_{c}": 0.0 for c in MONEY_COLS
                    if c in cols and c not in ("total_equity", "available_balance")}
                   | {"bal": bal, "i": acct})
        db.commit()
        after = db.execute(text(
            "SELECT * FROM arbitrage_paper_accounts WHERE id=:i"), {"i": acct}).first()
        print("重置后: " + str({c: getattr(after, c) for c in cols}))

    # meta：shadow_equity=300 + stats_since=now + 审计
    from backend.services import lane_registry as reg  # noqa: E402
    lane = reg.get_lane(lane_id)
    if not lane:
        print(f"✗ 车道不存在: {lane_id}")
        return 2
    meta = dict(lane.get("meta") or {})
    old_since, old_eq = meta.get("stats_since"), meta.get("shadow_equity")
    now_iso = datetime.now(timezone.utc).astimezone().isoformat()
    meta["stats_since"] = now_iso
    meta["shadow_equity"] = bal
    ops = meta.get("ops_changes")
    if not isinstance(ops, list):
        ops = []
    ops.append({"ts": now_iso, "op": "reset_account",
                "account_id": acct, "balance": bal,
                "old_stats_since": str(old_since), "old_shadow_equity": old_eq,
                "before": {k: (str(v) if hasattr(v, "isoformat") else v)
                           for k, v in before.items()},
                "reason": "用户批准：重置模拟账户至 300，统计时代起点重设"})
    meta["ops_changes"] = ops[-20:]
    ok = reg.update_meta(lane_id, meta)
    print(f"meta: shadow_equity {old_eq} → {bal}；stats_since {old_since} → {now_iso}"
          f"  写入={ok}")
    print("下一步：重启后端，核对 权益=300、腿量=0.1×300=30、车道恢复报价。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
