from sqlalchemy import create_engine, text
from collections import defaultdict
import numpy as np, datetime as dt
ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
# 日线缓存
data={}
with MARKET.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    for s, in c.execute(text("select distinct symbol from crypto_klines where period='1d' and exchange='asterdex'")):
        rows=c.execute(text("""select timestamp, close_price from crypto_klines
                               where period='1d' and exchange='asterdex' and symbol=:s order by timestamp"""),{"s":s}).fetchall()
        if len(rows)>100:
            data[s]=(np.array([int(r[0]) for r in rows]), np.array([float(r[1]) for r in rows]))
def regime_at(sym, ts):
    d=data.get(sym)
    if d is None: return None
    t,c=d
    i=np.searchsorted(t, ts, side='right')-1
    if i<60 or i>=len(c): return None
    ema=np.mean(c[max(0,i-200):i+1]) if i>=200 else np.mean(c[:i+1])
    mom60 = (c[i]/c[i-60]-1) if i>=60 and c[i-60]>0 else 0
    above = c[i] > ema
    if above and mom60>0.05: return "up"
    if (not above) and mom60<-0.05: return "down"
    return "chop"
with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,close_price,original_size,size,partial_realized_pnl,
               timeframe_tier,close_reason,opened_at
        from paper_positions where timeframe_tier in ('mid','long') and status='closed' order by opened_at
    """)).fetchall()]
def pnl(r):
    e=float(r['entry_price'] or 0); cp=float(r['close_price'] or 0); sz=float(r['original_size'] or r['size'] or 0)
    if e<=0 or cp<=0 or sz<=0: return None
    sgn=1 if r['side']=='long' else -1
    return (cp-e)*sgn*sz + float(r['partial_realized_pnl'] or 0)
g=defaultdict(list); unk=0
for r in rows:
    p=pnl(r)
    if p is None: continue
    reg=regime_at(r['symbol'], int(r['opened_at'].timestamp()))
    if reg is None: unk+=1; continue
    g[(reg, r['side'])].append(p)
print(f"可归类 {sum(len(v) for v in g.values())} 笔，无法归类 {unk} 笔")
print(f"\n{'regime':<7}{'方向':<7}{'n':>4}{'合计':>10}{'均值':>9}{'胜率':>7}")
total_actual=0; total_gated=0
for reg in ("up","chop","down"):
    for side in ("long","short"):
        v=g.get((reg,side)) or []
        total_actual += sum(v)
        keep = (reg=="up" and side=="long") or (reg=="down" and side=="short") or (reg=="chop" and side=="long")
        if keep: total_gated += sum(v)
        if v:
            w=sum(1 for x in v if x>0)
            print(f"{reg:<7}{side:<7}{len(v):>4}{sum(v):>10.2f}{sum(v)/len(v):>9.3f}{w/len(v):>7.3f}{'  ← 保留' if keep else '  ← 剔除'}")
print(f"\n实际总 PnL: {total_actual:+.2f}")
print(f"regime 门后总 PnL: {total_gated:+.2f}  (改善 {total_gated-total_actual:+.2f})")
