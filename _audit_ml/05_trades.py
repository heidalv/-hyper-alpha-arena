from sqlalchemy import create_engine, text
import statistics as st, json
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
def rows(c, sql, **p): return [dict(r._mapping) for r in c.execute(text(sql), p).fetchall()]
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### strategy_trades columns sample")
    r = rows(c, "select * from strategy_trades order by id desc limit 3")
    for x in r: print(json.dumps({k:(str(v)[:200] if v is not None else None) for k,v in x.items()}, ensure_ascii=False)[:2000]); print("---")
    print("### strategy_trades by strategy_id")
    for x in rows(c, """select strategy_id, count(*) n, round(sum(pnl)::numeric,2) sum_pnl, round(avg(pnl)::numeric,3) avg,
                               count(*) filter (where pnl>0) wins, min(opened_at) mn, max(closed_at) mx
                        from strategy_trades group by 1 order by n desc limit 30"""):
        print(x)
    print("\n### decision_source distribution")
    for x in rows(c, "select decision_source, count(*) n, round(sum(pnl)::numeric,2) s, round(avg(pnl)::numeric,3) a from strategy_trades group by 1 order by n desc limit 30"):
        print(x)
    print("\n### trade_facts by tier/source")
    for x in rows(c, """select tier, source, count(*) n, round(sum(pnl)::numeric,2) s, round(avg(pnl)::numeric,4) a,
                               count(*) filter (where outcome='win') w
                        from trade_facts group by 1,2 order by n desc"""):
        print(x)
    print("\n### trade_facts by tier x close_reason (mid/long)")
    for x in rows(c, """select tier, close_reason, count(*) n, round(sum(pnl)::numeric,2) s, round(avg(pnl)::numeric,4) a,
                               round(sum(fees)::numeric,3) fees
                        from trade_facts where tier in ('mid','long') group by 1,2 order by n desc limit 40"""):
        print(x)
