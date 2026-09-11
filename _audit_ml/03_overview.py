from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
def q(c, sql, **p):
    return c.execute(text(sql), p).fetchall()
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### accounts")
    for r in q(c,"select id,name,account_type,is_active,trading_mode,selected_exchange,initial_capital,current_cash from accounts order by id"):
        print(r)
    print("\n### paper_positions overview")
    for r in q(c,"""select count(*) n, min(opened_at) mn, max(opened_at) mx,
                            count(*) filter (where status='open') n_open from paper_positions"""):
        print(r)
    print("\n### by timeframe_tier x trade_nature x status")
    for r in q(c,"""select timeframe_tier, trade_nature, status, count(*) n
                    from paper_positions group by 1,2,3 order by 1,2,3"""):
        print(r)
    print("\n### by account x tier (closed only)")
    for r in q(c,"""select account_id, timeframe_tier, count(*) n,
                           count(*) filter (where (coalesce(partial_realized_pnl,0) + case when status='closed' then 0 else 0 end) > 0) dummy
                    from paper_positions group by 1,2 order by 1,2"""):
        print(r)
