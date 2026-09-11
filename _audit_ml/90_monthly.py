from sqlalchemy import create_engine, text
import json
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### trade_facts by tier x month")
    for r in c.execute(text("""select tier, to_char(ts,'YYYY-MM') m, count(*) n, round(sum(pnl)::numeric,2) s,
                                      round(avg(pnl)::numeric,3) a, count(*) filter (where outcome='win') w
                               from trade_facts group by 1,2 order by 2,1""")):
        print(dict(r._mapping))
    print("\n### paper_positions mid/long by month (realized proxy)")
    for r in c.execute(text("""select timeframe_tier, to_char(closed_at,'YYYY-MM') m, count(*) n,
                                      round(sum(coalesce(partial_realized_pnl,0) +
                                        (case when side='long' then (close_price-entry_price) else (entry_price-close_price) end)
                                        * coalesce(original_size,size))::numeric,2) pnl
                               from paper_positions where timeframe_tier in ('mid','long') and status='closed'
                               group by 1,2 order by 2,1""")):
        print(dict(r._mapping))
