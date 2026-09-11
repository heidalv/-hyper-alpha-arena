# -*- coding: utf-8 -*-
"""P5d 车道级真实费率比：sum(fee)/sum(|名义|) on filled scalp_mr_ orders。"""
from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = [r for r in c.execute(text("""
        select exchange, count(*) as n, sum(fee) as fee, sum(abs(filled_price*filled_quantity)) as notional
        from paper_orders
        where strategy_id like 'scalp_mr_%' and status='filled' and fee is not null
          and filled_price is not null and filled_quantity is not null
        group by exchange
    """)).fetchall()]
    for r in rows:
        ratio = float(r[2]) / float(r[3]) * 10000 if r[3] else 0
        print(f"{r[0]:>10}: orders={r[1]:>5} fee={float(r[2]):>8.2f} notional={float(r[3]):>12.0f} fee_bp={ratio:.2f}")
