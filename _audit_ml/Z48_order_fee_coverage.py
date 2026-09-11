# -*- coding: utf-8 -*-
"""Z48：paper_orders 的费用/盈亏记录完整度——判定「权益是否真的扣了交易费」。

Z47 发现：账户 14 生命周期 3045 笔持仓，`total_fee_paid` 仅 $42.15，
而按 0.1%×名义估算应有 ~$750。权益恒等式 `equity = initial + Σorder.pnl + funding − Σorder.fee`
成立，所以问题落在 **`Σorder.fee` 是否真实**。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
ACCT = 14


def main() -> int:
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        cols = [r[0] for r in c.execute(text("""
            select column_name from information_schema.columns
            where table_name='paper_orders' order by ordinal_position
        """)).fetchall()]
        print("paper_orders 列:", ", ".join(cols))

        r = c.execute(text("""
            select count(*) n,
                   count(fee) n_fee_notnull,
                   sum(case when coalesce(fee,0) > 0 then 1 else 0 end) n_fee_pos,
                   round(coalesce(sum(fee),0)::numeric,2) sum_fee,
                   count(pnl) n_pnl_notnull,
                   round(coalesce(sum(pnl),0)::numeric,2) sum_pnl
            from paper_orders where account_id=:a
        """), {"a": ACCT}).mappings().first()
        print("\n=== 账户 14 订单汇总 ===")
        for k, v in r.items():
            print(f"  {k} = {v}")

        print("\n=== 按 side / 订单类型看费用记录 ===")
        for rr in c.execute(text("""
            select side, count(*) n,
                   sum(case when coalesce(fee,0)>0 then 1 else 0 end) n_pos,
                   round(coalesce(sum(fee),0)::numeric,2) s_fee,
                   round(coalesce(avg(fee),0)::numeric,4) avg_fee,
                   round(coalesce(sum(price*filled_quantity),0)::numeric,0) notional
            from paper_orders where account_id=:a group by 1 order by 2 desc
        """), {"a": ACCT}).fetchall():
            print(f"  side={rr[0]:<6} n={rr[1]:<6} fee>0 的={rr[2]:<6} Σfee={rr[3]:>9} "
                  f"avg_fee={rr[4]:>8} Σ名义={rr[5]:>10}")

        print("\n=== 最近 10 笔订单 ===")
        for rr in c.execute(text("""
            select id, symbol, side, order_type, price, filled_quantity, fee, pnl, created_at
            from paper_orders where account_id=:a order by id desc limit 10
        """), {"a": ACCT}).fetchall():
            print(f"  #{rr[0]:<6}{str(rr[1]):<9}{str(rr[2]):<6}{str(rr[3]):<10}"
                  f"px={rr[4]} qty={rr[5]} fee={rr[6]} pnl={rr[7]} {str(rr[8])[:19]}")

        print("\n=== 结论判据 ===")
        if r["n"] and float(r["sum_fee"] or 0) > 0:
            per = float(r["sum_fee"]) / max(r["n"], 1)
            print(f"  平均每单费用 = ${per:.4f}（订单 {r['n']} 单）")
            print(f"  fee>0 的订单占比 = {(r['n_fee_pos'] or 0)/max(r['n'],1):.1%}")
        print("  若「fee>0 占比」远低于 100% 或平均费用远低于 0.05%×名义，"
              "则**平仓费用未被记账** ⇒ 权益虚高。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
