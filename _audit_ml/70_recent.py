from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### mid/long positions opened in last 10 days")
    for r in c.execute(text("""select id,account_id,symbol,side,timeframe_tier,trade_nature,status,opened_at,closed_at,close_reason,
                                      round(coalesce(partial_realized_pnl,0)::numeric,2) pnl
                               from paper_positions where timeframe_tier in ('mid','long')
                                 and opened_at > now() - interval '10 days'
                               order by opened_at desc limit 40""")):
        print(dict(r._mapping))
    print("\n### open mid/long now")
    for r in c.execute(text("""select id,account_id,symbol,side,timeframe_tier,trade_nature,opened_at,unrealized_pnl,margin,leverage
                               from paper_positions where timeframe_tier in ('mid','long') and status='open' order by opened_at desc""")):
        print(dict(r._mapping))
    print("\n### midlong circuit state")
    import os,json
    p='data/midlong_circuit_state.json'
    if os.path.exists(p): print(json.dumps(json.load(open(p,encoding='utf-8')),ensure_ascii=False,indent=2)[:1500])
