from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for r in c.execute(text("""select close_reason, count(*) n, round(sum(coalesce(partial_realized_pnl,0))::numeric,2) p
                               from paper_positions where timeframe_tier in ('mid','long') and status='closed'
                               group by 1 order by n desc limit 40""")):
        print(r)
