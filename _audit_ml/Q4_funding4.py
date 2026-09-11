from sqlalchemy import create_engine, text
import statistics as st
from collections import defaultdict
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    rows = c.execute(text("""
        select symbol, funding_rate::text as fr
        from perp_funding
        where exchange='asterdex'
          and timestamp > (extract(epoch from now())*1000 - 30*86400*1000)
    """)).fetchall()
print("样本:", len(rows))
by=defaultdict(list)
for s,fr in rows:
    try: by[s].append(float(fr))
    except Exception: pass
print("可解析:", sum(len(v) for v in by.values()))
allv=[x for v in by.values() for x in v]
print(f"\n=== 全市场 funding（asterdex 近30天）===")
print(f"  n={len(allv)} 均值={st.mean(allv)*100:.5f}% 中位={st.median(allv)*100:.5f}%")
print(f"  正费率占比={sum(1 for x in allv if x>0)/len(allv):.3f}")
print(f"  年均化(均值)={st.mean(allv)*3*365*100:.2f}%")
print(f"\n{'币':<10}{'n':>7}{'均值%':>10}{'中位%':>10}{'年均化%':>10}")
apr=[]
for s,v in by.items():
    if len(v)<200: continue
    m=st.mean(v); apr.append((m*3*365*100, s, m, st.median(v), len(v)))
apr.sort(reverse=True)
for ann,s,m,med,n in apr[:15]:
    print(f"{s:<10}{n:>7}{m*100:>10.5f}{med*100:>10.5f}{ann:>10.2f}")
print("\n  最低 5 个:")
for ann,s,m,med,n in apr[-5:]:
    print(f"{s:<10}{n:>7}{m*100:>10.5f}{med*100:>10.5f}{ann:>10.2f}")
