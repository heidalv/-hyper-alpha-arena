from sqlalchemy import create_engine, text
from collections import Counter, defaultdict
import json
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### 按 phase × action 计数（全部）")
    for r in c.execute(text("""select phase, action, count(*) n, count(distinct factor_id) f,
                                      min(created_at) mn, max(created_at) mx
                               from factor_evolution_log group by 1,2 order by n desc limit 30""")):
        print(dict(r._mapping))
    print("\n### 近 30 天 phase × action")
    for r in c.execute(text("""select phase, action, count(*) n, count(distinct factor_id) f
                               from factor_evolution_log where created_at > now() - interval '30 days'
                               group by 1,2 order by n desc limit 30""")):
        print(dict(r._mapping))
    print("\n### wfo_reject 原因前缀分布（近 30 天）")
    cnt=Counter()
    for (reason,) in c.execute(text("""select reason from factor_evolution_log
                                       where action like '%reject%' and created_at > now() - interval '30 days' limit 20000""")):
        s=str(reason or '')
        key=s.split(':')[1] if ':' in s else s[:40]
        cnt[key]+=1
    for k,v in cnt.most_common(15): print(f"   {k}: {v}")
    print("\n### source 分布（近 30 天）")
    for r in c.execute(text("""select source, count(*) n, count(distinct factor_id) f
                               from factor_evolution_log where created_at > now() - interval '30 days'
                               group by 1 order by n desc limit 15""")):
        print(dict(r._mapping))
