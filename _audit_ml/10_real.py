from sqlalchemy import create_engine, text
import json, statistics as st, math
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
def parse(v):
    if v is None: return {}
    if isinstance(v,dict): return v
    try: return json.loads(v)
    except Exception:
        try: return eval(v)
        except Exception: return {}
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,exit_price,position_size,leverage,pnl,pnl_pct,holding_period,
               decision_context,signal_context,ai_reasoning,opened_at,closed_at,strategy_id,tenant_id
        from strategy_trades order by id desc limit 2600
    """)).fetchall()]
    sel=[]
    for r in rows:
        d=parse(r['decision_context'])
        if str(r['strategy_id'] or '').startswith('e2e_'): continue
        if d.get('nature') in ('swing','trend_follow','position'):
            r['d']=d; r['s']=parse(r['signal_context']); sel.append(r)
    print("real mid/long strategy_trades:", len(sel))
    g={}
    for r in sel: g.setdefault((r['d'].get('nature'),r['side']),[]).append(float(r['pnl'] or 0))
    for k in sorted(g,key=str):
        v=g[k]; w=[x for x in v if x>0]
        print(f"  {k}: n={len(v)} sum={round(sum(v),2)} avg={round(sum(v)/len(v),3)} wr={round(len(w)/len(v),3)}")
    print("\n### by close_reason")
    g={}
    for r in sel: g.setdefault(r['d'].get('close_reason'),[]).append(float(r['pnl'] or 0))
    for k,v in sorted(g.items(), key=lambda kv:-len(kv[1])):
        w=[x for x in v if x>0]
        print(f"  {str(k)[:40]}: n={len(v)} sum={round(sum(v),2)} avg={round(sum(v)/len(v),3)} wr={round(len(w)/len(v),3)}")
    print("\n### by side x confidence")
    g={}
    for r in sel: g.setdefault((r['side'],r['d'].get('confidence')),[]).append(float(r['pnl'] or 0))
    for k in sorted(g,key=str):
        v=g[k]; print(f"  {k}: n={len(v)} avg={round(sum(v)/len(v),3)} sum={round(sum(v),2)}")
    print("\n### hold hours")
    hs=[((r['closed_at']-r['opened_at']).total_seconds()/3600, float(r['pnl'] or 0)) for r in sel if r['closed_at'] and r['opened_at']]
    if hs:
        print("  n=",len(hs),"med=",round(st.median([h for h,_ in hs]),2),"mean=",round(sum(h for h,_ in hs)/len(hs),2))
        for lo,hi,label in [(0,0.5,'<30min'),(0.5,2,'0.5-2h'),(2,8,'2-8h'),(8,24,'8-24h'),(24,72,'1-3d'),(72,1e9,'>3d')]:
            v=[p for h,p in hs if lo<=h<hi]
            if v: print(f"  {label}: n={len(v)} sum={round(sum(v),2)} avg={round(sum(v)/len(v),3)} wr={round(sum(1 for x in v if x>0)/len(v),3)}")
    print("\n### signal feature corr with pnl (real)")
    feats={}
    for r in sel:
        for k,v in r['s'].items():
            if isinstance(v,(int,float)) and not isinstance(v,bool):
                feats.setdefault(k,[]).append((float(v),float(r['pnl'] or 0)))
    def corr(xs,ys):
        n=len(xs)
        if n<10: return None
        mx=sum(xs)/n; my=sum(ys)/n
        num=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
        dx=math.sqrt(sum((x-mx)**2 for x in xs)); dy=math.sqrt(sum((y-my)**2 for y in ys))
        return None if dx==0 or dy==0 else num/(dx*dy)
    for k,pairs in sorted(feats.items(), key=lambda kv:-len(kv[1])):
        if len(pairs)<15: continue
        c=corr([p[0] for p in pairs],[p[1] for p in pairs])
        print(f"  {k}: corr={None if c is None else round(c,4)} n={len(pairs)}")
    print("\n### symbol")
    g={}
    for r in sel: g.setdefault(r['symbol'],[]).append(float(r['pnl'] or 0))
    for k,v in sorted(g.items(), key=lambda kv:-len(kv[1]))[:20]:
        print(f"  {k}: n={len(v)} sum={round(sum(v),2)} avg={round(sum(v)/len(v),3)}")
