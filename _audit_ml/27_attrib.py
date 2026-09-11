from sqlalchemy import create_engine, text
import json, statistics as st
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### brain_attribution mid+llm sample")
    for r in c.execute(text("""select * from brain_attribution where tier='mid' and src='llm' order by id desc limit 8""")):
        print(dict(r._mapping))
    print("\n### brain_attribution mid+llm stats")
    for r in c.execute(text("""select count(*) n, count(*) filter (where win) wins, count(*) filter (where net=0) zero,
                                      count(*) filter (where net is null) nulls,
                                      min(created_at), max(created_at),
                                      round(avg(pnl)::numeric,3) avg_pnl, round(avg(fee)::numeric,4) avg_fee,
                                      round(avg(net)::numeric,3) avg_net
                               from brain_attribution where tier='mid' and src='llm'""")):
        print(dict(r._mapping))
    print("\n### all brain_attribution net=0 share")
    for r in c.execute(text("""select tier, src, count(*) n, count(*) filter (where net=0) zero_net,
                                      round(100.0*count(*) filter (where net=0)/count(*),1) pct_zero
                               from brain_attribution group by 1,2 order by n desc""")):
        print(dict(r._mapping))
    print("\n### brain_episodes net vs win consistency")
    for r in c.execute(text("""select count(*) n, count(*) filter (where win) wins, count(*) filter (where net>0) net_pos,
                                      count(*) filter (where win and net<=0) win_but_neg,
                                      count(*) filter (where not win and net>0) loss_but_pos
                               from brain_episodes""")):
        print(dict(r._mapping))
