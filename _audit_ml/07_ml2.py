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
               decision_context,signal_context,ai_reasoning,opened_at,closed_at,strategy_id
        from strategy_trades
        where cast(decision_context as text) like '%swing%' or cast(decision_context as text) like '%trend_follow%'
        order by closed_at desc
    """)).fetchall()]
    print("rows:", len(rows))
    for r in rows:
        r['d']=parse(r['decision_context']); r['s']=parse(r['signal_context'])
    print("\n### tier x nature x side")
    g={}
    for r in rows:
        g.setdefault((r['d'].get('tier'),r['d'].get('nature'),r['side']),[]).append(float(r['pnl'] or 0))
    for k in sorted(g,key=str):
        v=g[k]; w=[x for x in v if x>0]
        print(f"  {k}: n={len(v)} sum={round(sum(v),2)} avg={round(sum(v)/len(v),3)} wr={round(len(w)/len(v),3)}")
    print("\n### date range", min(r['opened_at'] for r in rows), max(r['closed_at'] for r in rows))
    print("\n### sample decision_context")
    print(json.dumps(rows[0]['d'], ensure_ascii=False)[:1200])
    print("\n### sample signal_context")
    print(json.dumps(rows[0]['s'], ensure_ascii=False)[:1200])
    print("\n### signal feature corr with pnl")
    feats={}
    for r in rows:
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
    res=[]
    for k,pairs in feats.items():
        if len(pairs)<20: continue
        c=corr([p[0] for p in pairs],[p[1] for p in pairs])
        if c is not None: res.append((abs(c),c,k,len(pairs)))
    for a,c,k,n in sorted(res,reverse=True)[:25]:
        print(f"  {k}: corr={round(c,4)} n={n}")
    print("\n### holding_period (seconds?) stats")
    hp=[r['holding_period'] for r in rows if r['holding_period'] is not None]
    if hp: print("  n=",len(hp),"zeros=",sum(1 for x in hp if x==0),"med=",st.median(hp),"max=",max(hp))
    print("\n### hold hours from timestamps")
    hs=[(r['closed_at']-r['opened_at']).total_seconds()/3600 for r in rows if r['closed_at'] and r['opened_at']]
    if hs: print("  n=",len(hs),"med=",round(st.median(hs),2),"mean=",round(sum(hs)/len(hs),2),"min=",round(min(hs),3),"max=",round(max(hs),1))
    print("\n### leverage x avg pnl")
    g={}
    for r in rows: g.setdefault(r['leverage'],[]).append(float(r['pnl'] or 0))
    for k in sorted(g,key=lambda x:(x is None,x)):
        v=g[k]; print(f"  lev={k}: n={len(v)} avg={round(sum(v)/len(v),3)} sum={round(sum(v),2)}")
    print("\n### pnl_pct (notional-based?) avg")
    pp=[float(r['pnl_pct']) for r in rows if r['pnl_pct'] is not None]
    print("  n=",len(pp),"avg=",round(sum(pp)/len(pp),5),"med=",round(st.median(pp),5))
    print("\n### regime x pnl")
    g={}
    for r in rows: g.setdefault((r['d'].get('regime'),r['side']),[]).append(float(r['pnl'] or 0))
    for k in sorted(g,key=str):
        v=g[k]; print(f"  {k}: n={len(v)} avg={round(sum(v)/len(v),3)} sum={round(sum(v),2)}")
    print("\n### confidence x pnl")
    g={}
    for r in rows: g.setdefault(r['d'].get('confidence'),[]).append(float(r['pnl'] or 0))
    for k in sorted(g,key=lambda x:(x is None,x)):
        v=g[k]; print(f"  conf={k}: n={len(v)} avg={round(sum(v)/len(v),3)} wr={round(sum(1 for x in v if x>0)/len(v),3)}")
