from sqlalchemy import create_engine, text
import statistics as st
from collections import defaultdict
import numpy as np
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
SYMS = ["BTC","ETH","SOL","BNB","XRP","DOGE","ADA","AVAX","LINK","DOT","LTC","TON","TRX","ATOM","BCH","ETC","UNI","AAVE","ARB","OP","SUI","APT","NEAR","INJ","SEI","CRV","ASTER","XPL","VIRTUAL","ZEC"]
data={}
with eng.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    for s in SYMS:
        rows=c.execute(text("""select timestamp, close_price from crypto_klines
                               where period='1d' and exchange='asterdex' and symbol=:s order by timestamp"""),{"s":s}).fetchall()
        if len(rows)>250:
            data[s]=(np.array([int(r[0]) for r in rows]), np.array([float(r[1]) for r in rows]))
print("有日线数据的币:", len(data))
# 对每个币、每一天：计算 regime（EMA200 上下、20日动量、60日动量）与未来 14 日收益
buckets=defaultdict(lambda: defaultdict(list))
for s,(ts,c) in data.items():
    n=len(c)
    ema200=np.zeros(n); k=2/201.0; ema200[0]=c[0]
    for i in range(1,n): ema200[i]=c[i]*k+ema200[i-1]*(1-k)
    for i in range(200, n-14):
        above = c[i] > ema200[i]
        mom20 = (c[i]/c[i-20]-1) if c[i-20]>0 else 0
        mom60 = (c[i]/c[i-60]-1) if c[i-60]>0 else 0
        fwd14 = (c[i+14]/c[i]-1)
        # regime 分类
        if above and mom60>0.05: reg="up"
        elif (not above) and mom60<-0.05: reg="down"
        else: reg="chop"
        buckets[reg]["long14"].append(fwd14)
        buckets[reg]["short14"].append(-fwd14)
        buckets[reg]["long7"].append((c[i+7]/c[i]-1))
        buckets[reg]["short7"].append(-(c[i+7]/c[i]-1))
print(f"\n{'regime':<8}{'方向':<7}{'n':>7}{'均值%':>9}{'中位%':>9}{'胜率':>7}{'t值':>7}")
for reg in ("up","chop","down"):
    for d in ("long","short"):
        for h in ("7","14"):
            v=buckets[reg].get(f"{d}{h}") or []
            if not v: continue
            a=np.array(v)*100; n=len(a); m=a.mean(); se=a.std(ddof=1)/np.sqrt(n)
            print(f"{reg:<8}{d+'-'+h+'d':<7}{n:>7}{m:>9.3f}{np.median(a):>9.3f}{(a>0).mean():>7.3f}{m/se:>7.2f}")
