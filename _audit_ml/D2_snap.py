from sqlalchemy import create_engine, text
import json
from collections import Counter, defaultdict
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### decision_snapshot 键分布（近 7 天 mid/long 决策）")
    cnt=Counter(); n=0
    for (ds,) in c.execute(text("""select decision_snapshot from ai_decision_logs
                                   where decision_time > now() - interval '7 days'
                                     and decision_snapshot is not null limit 5000""")):
        try:
            d=json.loads(ds) if isinstance(ds,str) else (ds or {})
        except Exception:
            continue
        n+=1
        for k in d: cnt[k]+=1
    print("  样本:", n)
    for k,v in cnt.most_common(30): print(f"   {k}: {v}")
    print("\n### 是否含 tier / direction / confidence / source")
    for r in c.execute(text("""select decision_snapshot from ai_decision_logs
                               where decision_time > now() - interval '2 days'
                                 and decision_snapshot like '%"tier"%' limit 2""")):
        d=json.loads(r[0]) if isinstance(r[0],str) else r[0]
        print(json.dumps(d, ensure_ascii=False)[:1200]); print("---")
