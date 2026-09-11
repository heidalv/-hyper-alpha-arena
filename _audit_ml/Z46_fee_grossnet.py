# -*- coding: utf-8 -*-
"""Z46：判定 `unrealized_pnl` 是「含费」还是「不含费」——决定总 USD 公式是否双计。

判据：用价格×数量算**毛盈亏**，与 `unrealized_pnl` 比：
  - 接近毛值 → 不含费（fees 单独列）→ 总USD 公式里的 −partial_fee_paid 是**必要**的；
  - 接近毛值−fee → 含费 → 再减 partial_fee_paid 会**双计**部分腿费用。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
FEE_SIDE = 0.0005


def main() -> int:
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = c.execute(text("""
            select id, symbol, side, entry_price, close_price, size, original_size,
                   unrealized_pnl, partial_realized_pnl, partial_fee_paid, leverage
            from paper_positions
            where account_id=14 and status='closed' and close_price>0
              and original_size>0 and partial_realized_pnl=0
            order by closed_at desc limit 8
        """)).fetchall()
        print(f"{'id':>6}{'sym':<9}{'side':<6}{'毛盈亏':>10}{'unrealized':>11}{'差(含费?)':>11}"
              f"{'双边费估':>10}{'partial_fee':>12}")
        tot_gross = tot_u = tot_estfee = 0.0
        for r in rows:
            pid, sym, side, ep, cp, sz = r[0], r[1], str(r[2]), float(r[3]), float(r[4]), float(r[6])
            sign = 1.0 if side == "long" else -1.0
            gross = (cp - ep) * sz * sign
            u = float(r[7] or 0)
            est_fee = (ep + cp) * sz * FEE_SIDE        # 开+平双边、单边 5bp
            print(f"{pid:>6}{sym:<9}{side:<6}{gross:>+10.3f}{u:>+11.3f}"
                  f"{u-gross:>+11.3f}{est_fee:>10.3f}{float(r[9] or 0):>12.4f}")
            tot_gross += gross
            tot_u += u
            tot_estfee += est_fee
        print(f"\n小计：毛={tot_gross:+.3f} unrealized={tot_u:+.3f} 差={tot_u-tot_gross:+.3f} "
              f"双边费估算={tot_estfee:.3f}")
        verdict = "不含费（fees 单独列）" if abs(tot_u - tot_gross) < tot_estfee * 0.5 else \
                  "疑似已含费（再减 partial_fee 会双计）"
        print(f"→ 判定：unrealized_pnl {verdict}")

        print("\n=== 账户级恒等式复核 ===")
        b = c.execute(text("""
            select initial_balance, total_equity, realized_pnl, unrealized_pnl, total_fee_paid
            from paper_balances where account_id=14
        """)).mappings().first()
        ib, eq = float(b["initial_balance"]), float(b["total_equity"])
        rp, up, fee = float(b["realized_pnl"]), float(b["unrealized_pnl"]), float(b["total_fee_paid"])
        calc = ib + rp + up
        print(f"  initial={ib:.4f} realized={rp:+.4f} unrealized={up:+.4f} fee={fee:.4f}")
        print(f"  initial+realized+unrealized = {calc:.4f}")
        print(f"  −fee                        = {calc - fee:.4f}   实际 equity = {eq:.4f}"
              f"   差 = {calc - fee - eq:+.4f}")
        print("  → " + ("恒等式成立：equity = initial + realized + unrealized − fee（费单独扣）"
                        if abs(calc - fee - eq) < 0.5 else "**不成立**，存在其它项/漂移"))

        print("\n=== 资金费流水（账户 14 / 全库）===")
        for r in c.execute(text("""
            select account_id, count(*) n, round(sum(payment)::numeric,4) s
            from paper_funding_ledger group by 1 order by 2 desc limit 5
        """)).fetchall():
            print(f"  acct={r[0]} n={r[1]} 合计={r[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
