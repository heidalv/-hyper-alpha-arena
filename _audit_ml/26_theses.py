from sqlalchemy import create_engine, text
import json
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### brain_theses by tier")
    for r in c.execute(text("""select tier, count(*) n, count(*) filter (where recommend_open) rec_open,
                                      count(*) filter (where should_close) should_close,
                                      round(avg(llm_conviction)::numeric,1) avg_conv,
                                      min(created_at), max(updated_at)
                               from brain_theses group by 1 order by n desc""")):
        print(dict(r._mapping))
    print("\n### brain_theses direction x tier")
    for r in c.execute(text("""select tier, direction, count(*) n, round(avg(llm_conviction)::numeric,1) conv,
                                      count(*) filter (where recommend_open) rec_open
                               from brain_theses group by 1,2 order by 1,3 desc""")):
        print(dict(r._mapping))
    print("\n### recent theses (mid/long)")
    for r in c.execute(text("""select thesis_id,symbol,tier,direction,llm_conviction,recommend_open,should_close,source,
                                      left(thesis_summary,160) summary, created_at, updated_at
                               from brain_theses where tier in ('mid','long') order by updated_at desc limit 12""")):
        print(dict(r._mapping))
    print("\n### brain_episodes by tier/win")
    for r in c.execute(text("""select e.decision_source, t.tier, count(*) n, round(sum(e.net)::numeric,2) net,
                                      round(avg(e.net)::numeric,3) avg_net,
                                      count(*) filter (where e.win) wins
                               from brain_episodes e left join brain_theses t on e.thesis_id=t.thesis_id
                               group by 1,2 order by n desc limit 20""")):
        print(dict(r._mapping))
    print("\n### brain_attribution by tier")
    for r in c.execute(text("""select tier, src, count(*) n, round(sum(net)::numeric,2) net, round(avg(net)::numeric,3) avg_net,
                                      count(*) filter (where win) wins
                               from brain_attribution group by 1,2 order by n desc limit 25""")):
        print(dict(r._mapping))
