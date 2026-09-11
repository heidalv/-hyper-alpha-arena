from sqlalchemy import create_engine, text
import statistics as st
from collections import defaultdict
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    rows = c.execute(text("""
        select symbol, funding_rate::float8 as fr
        from perp_funding
        where exchange='asterdex'
          and timestamp > (extract(epoch from now())*1000 - 30*86400*1000)
    """)).fetchall()
print("样本:", len(rows))
by=defaultdict(list)
for s,fr in rows: by[s].append(float(fr))
print(f"\n{'币':<10}{'n':>7}{'均值%':>9}{'中位%':>9}{'p90%':>9}{'年均化%':>10}")
apr=[]
for s,v in sorted(by.items(), key=lambda kv:-len(kv[1])):
    if len(v)<200: continue
    m=st.mean(v); med=st.median(v); p90=sorted(v)[int(0.9*len(v))]
    ann=m*3*365*100
    apr.append((ann,s,m,med,p90,len(v)))
apr.sort(reverse=True)
for ann,s,m,med,p90,n in apr[:20]:
    print(f"{s:<10}{n:>7}{m*100:>9.5f}{med*100:>9.5f}{p90*100:>9.5f}{ann:>10.2f}")
print("\n=== 全市场 funding 统计 ===")
allv=[x for v in by.values() for x in v]
print(f"  n={len(allv)} 均值={st.mean(allv)*100:.5f}% 中位={st.median(allv)*100:.5f}%")
print(f"  正费率占比={sum(1 for x in allv if x>0)/len(allv):.3f}")
print(f"  年均化(全市场均值)={st.mean(allv)*3*365*100:.2f}%")
