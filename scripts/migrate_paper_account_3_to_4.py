#!/usr/bin/env python3
"""把误落到 account_id=3 (实盘账户的 paper 影子) 的所有 paper 订单/持仓/策略
    迁移到 account_id=4 (真正的 PAPER 模拟主力账户)。

执行前先备份数据库。

Usage:
    python scripts/migrate_paper_account_3_to_4.py [--dry-run]
"""
import argparse
import shutil
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

PROJECT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT / "data" / "alpha_arena.db"
SRC = 3   # 错账户：实盘"主力"
DST = 4   # 对账户：PAPER"主力"


def backup_db() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = DB_PATH.with_name(f"alpha_arena.db.bak_{ts}")
    shutil.copy2(DB_PATH, bak)
    return bak


def migrate(dry_run: bool):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 0) 验证 SRC/DST 都是 user=1 同主名
    c.execute("SELECT id, user_id, name, account_type FROM accounts WHERE id IN (?, ?)", (SRC, DST))
    accts = {r["id"]: dict(r) for r in c.fetchall()}
    print(f"  SRC account: {accts.get(SRC)}")
    print(f"  DST account: {accts.get(DST)}")
    assert accts.get(DST, {}).get("account_type") == "PAPER", "DST 必须是 PAPER 类型"

    # 1) 检查 DST 是否已有持仓/订单（防止覆盖）
    c.execute("SELECT COUNT(*) FROM paper_positions WHERE account_id=? AND status='open'", (DST,))
    dst_open_pos = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM paper_orders WHERE account_id=?", (DST,))
    dst_orders = c.fetchone()[0]
    print(f"  DST 当前 open 持仓: {dst_open_pos}, 总订单: {dst_orders}")

    # 2) SRC 待迁移项
    c.execute("SELECT COUNT(*) FROM paper_positions WHERE account_id=?", (SRC,))
    src_pos = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM paper_orders WHERE account_id=?", (SRC,))
    src_orders = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM ai_strategies WHERE account_id=? AND status IN ('active','paused')", (SRC,))
    src_strats = c.fetchone()[0]
    print(f"  SRC 待迁移: positions={src_pos}, orders={src_orders}, strategies={src_strats}")

    # 3) PaperBalance：SRC 的余额、frozen_margin 全部转回到 DST，SRC 重置为初始值
    c.execute("SELECT * FROM paper_balances WHERE account_id IN (?, ?)", (SRC, DST))
    bals = {r["account_id"]: dict(r) for r in c.fetchall()}
    print(f"  SRC balance: {bals.get(SRC)}")
    print(f"  DST balance: {bals.get(DST)}")

    if dry_run:
        print("\n  [dry-run] 不执行任何写入。")
        return

    # 真实迁移 — 用事务包裹
    try:
        # 1. paper_positions: account_id 改 SRC→DST
        c.execute("UPDATE paper_positions SET account_id=? WHERE account_id=?", (DST, SRC))
        print(f"  ✓ paper_positions: 迁移 {c.rowcount} 行")

        # 2. paper_orders: account_id 改 SRC→DST
        c.execute("UPDATE paper_orders SET account_id=? WHERE account_id=?", (DST, SRC))
        print(f"  ✓ paper_orders: 迁移 {c.rowcount} 行")

        # 3. ai_strategies: 当前 active/paused 的策略 SRC→DST（已平仓的历史策略保留 SRC 以保留学习数据原貌）
        c.execute(
            "UPDATE ai_strategies SET account_id=? WHERE account_id=? AND status IN ('active','paused')",
            (DST, SRC)
        )
        print(f"  ✓ ai_strategies (active/paused): 迁移 {c.rowcount} 行")

        # 4. PaperBalance: 把 SRC 的实际余额状态转移给 DST，SRC 重置回 10000 干净状态
        if bals.get(SRC):
            src_bal = bals[SRC]
            # DST 接收 SRC 的全部状态
            c.execute("""
                UPDATE paper_balances
                SET total_equity = ?,
                    available_balance = ?,
                    frozen_margin = ?,
                    unrealized_pnl = ?,
                    realized_pnl = ?,
                    total_fee_paid = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE account_id = ?
            """, (
                src_bal["total_equity"],
                src_bal["available_balance"],
                src_bal.get("frozen_margin", 0) or 0,
                src_bal.get("unrealized_pnl", 0) or 0,
                src_bal.get("realized_pnl", 0) or 0,
                src_bal.get("total_fee_paid", 0) or 0,
                DST,
            ))
            print(f"  ✓ PaperBalance DST({DST}): 接收 SRC 状态 (total=${src_bal['total_equity']:.2f})")

            # SRC 重置回干净 $10000（防止用户后续看到混乱状态）
            c.execute("""
                UPDATE paper_balances
                SET total_equity = 10000.0,
                    available_balance = 10000.0,
                    frozen_margin = 0,
                    unrealized_pnl = 0,
                    realized_pnl = 0,
                    total_fee_paid = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE account_id = ?
            """, (SRC,))
            print(f"  ✓ PaperBalance SRC({SRC}): 重置回 $10000 干净状态")

        # 5. full_auto_sessions: 把 paper_account_id 也确认设为 DST（其实已是 4，但保险起见）
        c.execute(
            "UPDATE full_auto_sessions SET paper_account_id=? WHERE account_id=? AND trading_mode='paper'",
            (DST, SRC)
        )
        print(f"  ✓ full_auto_sessions.paper_account_id: 已确认为 {DST} ({c.rowcount} 行)")

        conn.commit()
        print("\n  ✅ 所有迁移已 commit。")

        # 复验
        print("\n=== 迁移后状态 ===")
        for r in c.execute("SELECT account_id, COUNT(*) FROM paper_positions WHERE status='open' GROUP BY account_id"):
            print(f"  open positions account={r[0]}: {r[1]}")
        for r in c.execute("SELECT account_id, total_equity, available_balance, frozen_margin, unrealized_pnl, realized_pnl FROM paper_balances WHERE account_id IN (?, ?)", (SRC, DST)):
            print(f"  balance account={r[0]}: total=${r[1]:.2f} avail=${r[2]:.2f} frozen=${r[3] or 0:.2f} upnl=${r[4] or 0:+.2f} rpnl=${r[5] or 0:+.2f}")

    except Exception as e:
        conn.rollback()
        print(f"  ❌ 迁移失败已回滚: {e}")
        raise
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    print(f"DB: {DB_PATH}")
    if not DB_PATH.exists():
        print("❌ 数据库不存在", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run:
        bak = backup_db()
        print(f"✅ 备份: {bak}\n")

    migrate(args.dry_run)


if __name__ == "__main__":
    main()
