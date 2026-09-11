from sqlalchemy import create_engine, text
from collections import defaultdict
import numpy as np
ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
data={}
with MARKET.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    for s, in c.execute(text("select distinct symbol from crypto_klines where period='1d' and exchange='asterdex'")):
        rows=c.execute(text("""select timestamp, close_price from crypto_klines
                               where period='1d' and exchange='asterdex' and symbol=:s order by timestamp"""),{"s":s}).fetchall()
        if len(rows)>100:
            data[s]=(np.array([int(r[0]) for r in rows]), np.array([float(r[1]) for r in rows]))
def reg(sym, ts):
    d=data.get(sym)
    if d is None: return None
    t,c=d
    i=np.searchsorted(t, ts, side='right')-1
    if i<60 or i>=len(c): return None
    ema=np.mean(c[max(0,i-200):i+1])
    m60=(c[i]/c[i-60]-1) if c[i-60]>0 else 0
    if c[i]>ema and m60>0.05: return "up"
    if c[i]<ema and m60<-0.05: return "down"
    return "chop"
with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,close_price,original_size,size,partial_realized_pnl,opened_at,
               timeframe_tier,close_reason
        from paper_positions where timeframe_tier in ('mid','long') and status='closed' order by opened_at
    """)).fetchall()]
def pnl(r):
    e=float(r['entry_price'] or 0); cp=float(r['close_price'] or 0); sz=float(r['original_size'] or r['size'] or 0)
    if e<=0 or cp<=0 or sz<=0: return None
    sgn=1 if r['side']=='long' else -1
    return (cp-e)*sgn*sz + float(r['partial_realized_pnl'] or 0)
recs=[]
for r in rows:
    p=pnl(r)
    if p is None: continue
    g=reg(r['symbol'], int(r['opened_at'].timestamp()))
    if g is None: continue
    recs.append((r['side'], p, g, r['timeframe_tier']))
print(f"样本 {len(recs)} 笔")
def ev(name, keep):
    kept=[p for side,p,g,t in recs if keep(side,g,t)]
    dr=[p for side,p,g,t in recs if not keep(side,g,t)]
    print(f"  {name:<44} 保留{len(kept):>3} 合计{sum(kept):>+8.2f} 均值{sum(kept)/len(kept) if kept else 0:>+7.3f} | 剔除{len(dr)} {sum(dr):>+8.2f}")
print("\n=== 方案对比（含 chop 空仓）===")
ev("A 只做多（全停空头）", lambda s,g,t: s=="long")
ev("B 全放行", lambda s,g,t: True)
ev("C regime 门（当前）", lambda s,g,t: (s=="long" and g!="down") or (s=="short" and g=="down"))
ev("F regime 门 + chop 空仓", lambda s,g,t: (s=="long" and g=="up") or (s=="short" and g=="down"))
ev("H regime 门 + chop 只做多（当前）", lambda s,g,t: (s=="long" and g!="down") or (s=="short" and g=="down"))
print("\n=== chop 期间成交明细 ===")
ch=[(s,p) for s,p,g,t in recs if g=="chop"]
print(f"  chop 笔数 {len(ch)} 合计 {sum(p for _,p in ch):+.2f} 多头 {sum(p for s,p in ch if s=='long'):+.2f} 空头 {sum(p for s,p in ch if s=='short'):+.2f}")
