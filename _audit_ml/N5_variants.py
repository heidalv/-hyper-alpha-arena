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
def feats(sym, ts):
    d=data.get(sym)
    if d is None: return None
    t,c=d
    i=np.searchsorted(t, ts, side='right')-1
    if i<60 or i>=len(c): return None
    ema200 = np.mean(c[max(0,i-200):i+1])
    mom20 = (c[i]/c[i-20]-1) if c[i-20]>0 else 0
    mom60 = (c[i]/c[i-60]-1) if i>=60 and c[i-60]>0 else 0
    return dict(above=bool(c[i]>ema200), mom20=mom20, mom60=mom60, px=float(c[i]))
with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,close_price,original_size,size,partial_realized_pnl,opened_at
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
    f=feats(r['symbol'], int(r['opened_at'].timestamp()))
    if f is None: continue
    recs.append((r['side'], p, f))
print(f"样本 {len(recs)} 笔")
def evaluate(name, keep_fn):
    kept=[p for side,p,f in recs if keep_fn(side,f)]
    dropped=[p for side,p,f in recs if not keep_fn(side,f)]
    n=len(kept); s=sum(kept)
    print(f"  {name:<46} 保留{n:>3}笔 合计{s:>+8.2f} 均值{s/n if n else 0:>+7.3f} | 剔除{len(dropped)}笔 {sum(dropped):>+8.2f}")
print("\n=== 方案对比 ===")
evaluate("A 现状：空头全停（只做多）", lambda side,f: side=="long")
evaluate("B 当前 conditional（4h空/24h跌2%）→ 等价于都放行", lambda side,f: True)
evaluate("C regime 门：up/chop 只做多，down 可做空", lambda side,f: (side=="long" and not (not f["above"] and f["mom60"]<-0.05)) or (side=="short" and (not f["above"] and f["mom60"]<-0.05)))
evaluate("D 只按 EMA200：价上只多，价下可空", lambda side,f: (side=="long" and f["above"]) or (side=="short" and not f["above"]))
evaluate("E 只按 60d 动量：mom60>0 只多，<0 可空", lambda side,f: (side=="long" and f["mom60"]>0) or (side=="short" and f["mom60"]<0))
evaluate("F 严格：up 多 / down 空 / chop 空仓", lambda side,f: (side=="long" and f["above"] and f["mom60"]>0.05) or (side=="short" and (not f["above"]) and f["mom60"]<-0.05))
evaluate("G 严格+chop只做多", lambda side,f: (side=="long" and not((not f["above"]) and f["mom60"]<-0.05)) or (side=="short" and (not f["above"]) and f["mom60"]<-0.05))
