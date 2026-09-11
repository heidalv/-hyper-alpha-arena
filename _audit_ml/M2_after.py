from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### sessions")
    for r in c.execute(text("select id,session_id,account_id,trading_mode,status,started_at,last_health_check_at from full_auto_sessions order by id desc limit 5")):
        print(dict(r._mapping))
    print("\n### 重启后新开仓（近 20 分钟）")
    for r in c.execute(text("""select id,account_id,symbol,side,timeframe_tier,trade_nature,status,opened_at,close_reason
                               from paper_positions where opened_at > now() - interval '20 minutes' order by id desc limit 15""")):
        print(dict(r._mapping))
    print("\n### 近 20 分钟平仓")
    for r in c.execute(text("""select id,symbol,timeframe_tier,close_reason,closed_at,round(coalesce(partial_realized_pnl,0)::numeric,2) pnl
                               from paper_positions where closed_at > now() - interval '20 minutes' order by id desc limit 15""")):
        print(dict(r._mapping))
