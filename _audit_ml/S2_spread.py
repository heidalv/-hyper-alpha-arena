from sqlalchemy import create_engine, text
import statistics as st
from collections import defaultdict
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    rows = c.execute(text("""
        select symbol, best_bid, best_ask, spread, bid_depth_5, ask_depth_5, bid_depth_10, ask_depth_10
        from market_orderbook_snapshots
        where exchange='asterdex' and timestamp > 1788900000000
    """)).fetchall()
print("快照数:", len(rows))
by=defaultdict(list)
for s,bb,ba,sp,bd5,ad5,bd10,ad10 in rows:
    try:
        bb=float(bb); ba=float(ba)
        if bb<=0 or ba<=0: continue
        by[s].append(((ba-bb)/bb*10000, float(bd5 or 0), float(ad5 or 0)))  # spread bp
    except Exception: pass
print(f"\n{'币':<10}{'n':>7}{'spread中位bp':>13}{'spread均值bp':>13}{'p10bp':>8}{'p90bp':>8}{'bid5$中位':>11}{'ask5$中位':>11}")
res=[]
for s,v in by.items():
    if len(v)<200: continue
    sp=sorted(x[0] for x in v)
    res.append((st.median(sp), s, len(v), st.mean(sp), sp[int(0.1*len(sp))], sp[int(0.9*len(sp))],
                st.median([x[1] for x in v]), st.median([x[2] for x in v])))
res.sort()
for med,s,n,mean,p10,p90,bd,ad in res:
    print(f"{s:<10}{n:>7}{med:>13.2f}{mean:>13.2f}{p10:>8.2f}{p90:>8.2f}{bd:>11.0f}{ad:>11.0f}")
