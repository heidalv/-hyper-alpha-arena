# -*- coding: utf-8 -*-
"""探查 paper_orders.fee 的口径：为什么台账费是 8bp×名义的 4.4 倍。"""
from sqlalchemy import create_engine, text
import numpy as np

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    # 费率分布
    rows = [r for r in c.execute(text("""
        select fee, filled_price, filled_quantity, leverage, exchange from paper_orders
        where strategy_id like 'scalp_mr_%' order by fee desc limit 10
    """)).fetchall()]
    print("fee 最大的 10 行:")
    for r in rows:
        print("  ", tuple(round(float(x), 6) if isinstance(x, (int, float)) else x for x in r))
    # fee 分位
    fees = [float(r[0]) for r in c.execute(text("""
        select fee from paper_orders where strategy_id like 'scalp_mr_%' and fee is not null
    """)).fetchall()]
    if fees:
        print("fee 分位: p50=%.4f p90=%.4f max=%.4f n=%d" % (
            np.percentile(fees, 50), np.percentile(fees, 90), max(fees), len(fees)))
    # 一个持仓的全部订单
    sid = [r[0] for r in c.execute(text("""
        select strategy_id from paper_orders where strategy_id like 'scalp_mr_%'
        group by strategy_id having count(*)>=4 limit 1
    """)).fetchall()]
    if sid:
        rows = [r for r in c.execute(text("""
            select side, order_type, fee, filled_price, filled_quantity, status, close_reason, exchange
            from paper_orders where strategy_id=:s order by id
        """), {"s": sid[0]}).fetchall()]
        print(f"\n持仓 {sid[0]} 的全部订单:")
        for r in rows:
            print("  ", r)
    # fee / (price*quantity) 比率分布
    ratios = [float(r[0]) for r in c.execute(text("""
        select fee / nullif(abs(filled_price * filled_quantity), 0) from paper_orders
        where strategy_id like 'scalp_mr_%' and fee is not null and filled_quantity is not null
    """)).fetchall()]
    ratios = [r for r in ratios if np.isfinite(r) and r > 0]
    if ratios:
        print("fee/名义 分位: p10=%.5f p50=%.5f p90=%.5f（bp: x10000）" % (
            np.percentile(ratios, 10), np.percentile(ratios, 50), np.percentile(ratios, 90)))
