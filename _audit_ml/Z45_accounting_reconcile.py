# -*- coding: utf-8 -*-
"""Z45：记账口径对账（第 5 轮 (e) 项）。

核心问题：**账实相符吗？**
  账户权益 ≈ 初始资金 + Σ 已平仓总USD + Σ 未实现 + Σ 资金费 − Σ 已计手续费？
若不成立，说明存在重复计/漏计——那会**推翻此前所有基于总 USD 的结论**。

同时核验三个子口径：
  1. `unrealized_pnl` 是否已含手续费（用 §38.5 那类「事件 fee vs partial_fee_paid」交叉验证）；
  2. `partial_fee_paid` 覆盖哪些腿；
  3. `paper_funding_ledger` 是否已被计入持仓/权益。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
ACCT = int(os.getenv("Z45_ACCT", "14"))


def cols(c, table):
    try:
        return [r[0] for r in c.execute(text(
            "select column_name from information_schema.columns where table_name=:t "
            "order by ordinal_position"), {"t": table}).fetchall()]
    except Exception as e:
        c.rollback()
        return [f"<err {str(e)[:40]}>"]


def main() -> int:
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        print("=== 表结构 ===")
        for t in ("paper_balances", "paper_funding_ledger", "paper_orders"):
            print(f"  {t}: {', '.join(cols(c, t)[:18])}")

        print(f"\n=== 账户 {ACCT} 权益 ===")
        for r in c.execute(text("""
            select * from paper_balances where account_id=:a
        """), {"a": ACCT}).mappings().fetchall():
            for k, v in r.items():
                if v is not None and k not in ("id", "tenant_id"):
                    print(f"  {k} = {v}")

        print(f"\n=== 持仓汇总（账户 {ACCT}）===")
        row = c.execute(text("""
            select count(*) n,
                   sum(case when status='closed' then 1 else 0 end) n_closed,
                   sum(case when status='open' then 1 else 0 end) n_open,
                   sum(coalesce(unrealized_pnl,0)) sum_u,
                   sum(coalesce(partial_realized_pnl,0)) sum_p,
                   sum(coalesce(partial_fee_paid,0)) sum_fee,
                   min(opened_at) first_open
            from paper_positions where account_id=:a
        """), {"a": ACCT}).mappings().first()
        for k, v in (row or {}).items():
            print(f"  {k} = {v}")
        total_usd = float(row["sum_u"] or 0) + float(row["sum_p"] or 0) - float(row["sum_fee"] or 0)
        print(f"  → 总USD（unrealized+partial−partial_fee）= {total_usd:+.4f}")

        print("\n=== 资金费流水 ===")
        try:
            r2 = c.execute(text("""
                select count(*) n, sum(coalesce(amount,0)) s
                from paper_funding_ledger where account_id=:a
            """), {"a": ACCT}).mappings().first()
            print(f"  n={r2['n']} 合计={float(r2['s'] or 0):+.4f}")
        except Exception as e:
            c.rollback()
            print(f"  读取失败: {str(e)[:120]}")
            try:
                r2b = c.execute(text("""
                    select count(*) n from paper_funding_ledger where account_id=:a
                """), {"a": ACCT}).mappings().first()
                print(f"  （仅计数）n={r2b['n']}")
            except Exception as e2:
                c.rollback()
                print(f"  再失败: {str(e2)[:80]}")

        print("\n=== 手续费口径交叉验证：取 5 笔已平仓，比对 pnl_pct_at_event / partial_fee_paid ===")
        for r in c.execute(text("""
            select p.id, p.symbol, p.status, p.unrealized_pnl, p.partial_realized_pnl,
                   p.partial_fee_paid, p.size, p.original_size,
                   e.event_type, e.exit_channel, e.pnl as ev_pnl, e.fee as ev_fee
            from paper_positions p
            left join position_exit_events e
              on e.position_id=p.id and e.event_type='final_trade_outcome'
            where p.account_id=:a and p.status='closed'
            order by p.closed_at desc limit 5
        """), {"a": ACCT}).fetchall():
            print(f"  #{r[0]} {r[1]:<8} u={r[3]} p={r[4]} fee={r[5]} | 事件 pnl={r[9]} fee={r[10]}")

        print("\n=== 权益 vs 交易流水对账（需要初始资金）===")
        print("  账实判据：equity ≈ 初始资金 + Σ总USD + Σ资金费")
        print(f"  Σ总USD = {total_usd:+.2f}")
        print("  （初始资金字段若在 accounts / paper_balances 的 initial_* 列中，见上方输出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
