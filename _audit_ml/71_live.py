from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### accounts (active)")
    for r in c.execute(text("select id,name,account_type,is_active,trading_mode,selected_exchange from accounts where is_active='true'")):
        print(dict(r._mapping))
    print("\n### live positions")
    for r in c.execute(text("select count(*) from positions")):
        print("positions:", r)
    print("\n### live_sub_positions")
    for r in c.execute(text("select account_id,symbol,side,count(*) n from live_sub_positions group by 1,2,3")):
        print(dict(r._mapping))
    print("\n### full_auto_sessions running")
    for r in c.execute(text("select id,session_id,account_id,paper_account_id,trading_mode,status,started_at from full_auto_sessions where status='running'")):
        print(dict(r._mapping))
    print("\n### trade_facts by source/tier (all time)")
    for r in c.execute(text("select source,tier,count(*) n,round(sum(pnl)::numeric,2) s from trade_facts group by 1,2 order by n desc")):
        print(dict(r._mapping))
