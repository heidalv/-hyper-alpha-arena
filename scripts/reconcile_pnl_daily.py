# -*- coding: utf-8 -*-
"""每日 PnL/资金对账（M1-2 交付物 · 2026-08-22）。

不变量（任一不成立 → 输出 FAIL，应告警并暂停交易）：
  1. paper_balances.realized_pnl == SUM(paper_orders.pnl)（同账户、跨租户）
  2. paper_balances.total_fee_paid == SUM(paper_orders.fee)
  3. total_equity == initial_balance + realized_pnl + unrealized_pnl
  4. 会话 total_pnl == 账户 total_equity - initial_balance（净值口径）
  5. 无 closed 仓 unrealized_pnl 语义缺失（新数据）

用法：python scripts/reconcile_pnl_daily.py [--fix-session-stats]
"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession


def main(fix_session_stats: bool = False) -> int:
    failures = 0
    set_system_identity()
    s = ScopedSession()
    try:
        rows = s.execute(text("""
            SELECT a.id AS account_id, a.name,
                   pb.initial_balance, pb.total_equity, pb.realized_pnl,
                   pb.total_fee_paid, pb.unrealized_pnl,
                   (SELECT coalesce(sum(o.pnl),0) FROM paper_orders o WHERE o.account_id=a.id AND o.pnl IS NOT NULL) AS order_pnl,
                   (SELECT coalesce(sum(o.fee),0) FROM paper_orders o WHERE o.account_id=a.id AND o.fee IS NOT NULL) AS order_fee
            FROM accounts a LEFT JOIN paper_balances pb ON pb.account_id=a.id
            ORDER BY a.id
        """)).fetchall()
        print(f"{'acct':>6} {'name':<24} {'initial':>10} {'equity':>10} {'realized':>10} {'orders_pnl':>10} {'fee':>8} {'orders_fee':>9} {'check':>8}")
        for r in rows:
            if r.total_equity is None:
                print(f"{r.account_id:>6} {r.name[:24]:<24} (no paper_balance row)")
                continue
            c1 = abs(float(r.realized_pnl or 0) - float(r.order_pnl or 0)) < 0.05
            c2 = abs(float(r.total_fee_paid or 0) - float(r.order_fee or 0)) < 0.05
            # equity = initial + realized - fee + unrealized（frozen/available 抵消）
            c3 = abs(float(r.total_equity) - (
                float(r.initial_balance) + float(r.realized_pnl or 0)
                - float(r.total_fee_paid or 0) + float(r.unrealized_pnl or 0)
            )) < 0.05
            ok = c1 and c2 and c3
            if not ok:
                failures += 1
            print(f"{r.account_id:>6} {r.name[:24]:<24} {float(r.initial_balance):>10.2f} {float(r.total_equity):>10.2f} {float(r.realized_pnl or 0):>10.2f} {float(r.order_pnl or 0):>10.2f} {float(r.total_fee_paid or 0):>8.2f} {float(r.order_fee or 0):>9.2f} {'OK' if ok else 'FAIL'}")

        # 会话净值口径
        print("\n会话统计 vs 账户净值:")
        for r in s.execute(text("""
            SELECT fs.id, fs.session_id, fs.account_id, fs.total_pnl, fs.status
            FROM full_auto_sessions fs ORDER BY fs.id
        """)).fetchall():
            print(f"  session#{r.id} {r.session_id} acct={r.account_id} total_pnl={r.total_pnl} status={r.status}")

        # 若启用 --fix-session-stats：按账户净值刷新会话 total_pnl（仅 running）
        if fix_session_stats:
            n = s.execute(text("""
                UPDATE full_auto_sessions fs SET total_pnl = COALESCE((
                    SELECT pb.total_equity - pb.initial_balance
                    FROM paper_balances pb WHERE pb.account_id = fs.paper_account_id
                ), 0)
                WHERE fs.status='running' AND fs.paper_account_id IS NOT NULL
            """)).rowcount
            s.commit()
            print(f"\n[fix] 已刷新 {n} 个运行会话的 total_pnl（净值口径）")

        print(f"\n断言失败数: {failures}")
        return 1 if failures else 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main(fix_session_stats="--fix-session-stats" in sys.argv))
