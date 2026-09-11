from sqlalchemy import create_engine, text
import json, statistics as st
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows=[dict(r._mapping) for r in c.execute(text("""
        select id,symbol,side,entry_price,exit_price,position_size,leverage,pnl,pnl_pct,holding_period,
               decision_context,signal_context,ai_reasoning,opened_at,closed_at,strategy_id
        from strategy_trades
        where decision_context::text like '%''tier'': ''swing''%' or decision_context::text like '%''nature'': ''swing''%'
           or decision_context::text like '%''tier'': ''trend_follow''%' or decision_context::text like '%''tier'': ''position''%'
        order by closed_at desc
    """)).fetchall()]
    print("mid/long strategy_trades:", len(rows))
    def dctx(r):
        try: return json.loads(r['decision_context']) if isinstance(r['decision_context'],str) else (r['decision_context'] or {})
        except Exception:
            try: return eval(r['decision_context']) if isinstance(r['decision_context'],str) else {}
            except Exception: return {}
    def sctx(r):
        try: return json.loads(r['signal_context']) if isinstance(r['signal_context'],str) else (r['signal_context'] or {})
        except Exception:
            try: return eval(r['signal_context']) if isinstance(r['signal_context'],str) else {}
            except Exception: return {}
    print("\n### tier x side")
    g={}
    for r in rows:
        d=dctx(r); g.setdefault((d.get('tier'),r['side']),[]).append(float(r['pnl'] or 0))
    for k in sorted(g,key=str):
        v=g[k]; w=[x for x in v if x>0]
        print(f"  {k}: n={len(v)} sum={round(sum(v),2)} avg={round(sum(v)/len(v),3)} wr={round(len(w)/len(v),3)}")
    print("\n### sample decision_context keys (first 3)")
    for r in rows[:3]:
        print(json.dumps(dctx(r), ensure_ascii=False)[:800]); print('---')
    print("\n### signal_context keys (first 2)")
    for r in rows[:2]:
        print(json.dumps(sctx(r), ensure_ascii=False)[:900]); print('---')
    print("\n### signal feature vs outcome correlation (mid/long)")
    import math
    feats={}
    for r in rows:
        s=sctx(r); pnl=float(r['pnl'] or 0)
        for k,v in s.items():
            if isinstance(v,(int,float)) and not isinstance(v,bool):
                feats.setdefault(k,[]).append((float(v),pnl))
    def corr(xs,ys):
        n=len(xs)
        if n<10: return None
        mx=sum(xs)/n; my=sum(ys)/n
        num=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
        dx=math.sqrt(sum((x-mx)**2 for x in xs)); dy=math.sqrt(sum((y-my)**2 for y in ys))
        if dx==0 or dy==0: return None
        return num/(dx*dy)
    res=[]
    for k,pairs in feats.items():
        if len(pairs)<20: continue
        xs=[p[0] for p in pairs]; ys=[p[1] for p in pairs]
        c=corr(xs,ys)
        if c is not None: res.append((abs(c),c,k,len(pairs)))
    res.sort(reverse=True)
    for a,c,k,n in res[:25]:
        print(f"  {k}: corr={round(c,4)} n={n}")
    print("\n### holding_period distribution (mid/long)")
    hp=[r['holding_period'] for r in rows if r['holding_period'] is not None]
    print("  n=",len(hp),"zeros=",sum(1 for x in hp if x==0), "med=", st.median(hp) if hp else None)
    print("\n### leverage distribution")
    g={}
    for r in rows: g.setdefault(r['leverage'],[]).append(float(r['pnl'] or 0))
    for k in sorted(g,key=lambda x:(x is None,x)):
        v=g[k]; print(f"  lev={k}: n={len(v)} avg={round(sum(v)/len(v),3)} sum={round(sum(v),2)}")
