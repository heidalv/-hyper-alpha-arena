from sqlalchemy import create_engine, text
import json, statistics as st
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
        if d.get('confidence')==0.5 and d.get('nature')=='swing':
            r['d']=d; r['s']=parse(r['signal_context']); sel.append(r)
    print("conf=0.5 swing rows:", len(sel))
    for r in sel[:6]:
        print(r['id'], r['symbol'], r['side'], 'lev',r['leverage'], 'pnl',round(float(r['pnl'] or 0),2),
              r['opened_at'],'->',r['closed_at'],'strat',r['strategy_id'],'tenant',r['tenant_id'])
        print('   dctx:', json.dumps(r['d'], ensure_ascii=False)[:700])
        print('   sctx:', json.dumps(r['s'], ensure_ascii=False)[:400])
        print('   ai:', (r['ai_reasoning'] or '')[:200])
    print("\n### by tenant"); g={}
    for r in sel: g.setdefault(r['tenant_id'],[]).append(float(r['pnl'] or 0))
    for k in sorted(g,key=lambda x:(x is None,x)): print(f"  tenant={k}: n={len(g[k])} sum={round(sum(g[k]),2)}")
    print("\n### by date"); g={}
    for r in sel: g.setdefault(str(r['opened_at'])[:10],[]).append(float(r['pnl'] or 0))
    for k in sorted(g): print(f"  {k}: n={len(g[k])} sum={round(sum(g[k]),2)}")
    print("\n### by symbol"); g={}
    for r in sel: g.setdefault(r['symbol'],[]).append(float(r['pnl'] or 0))
    for k,v in sorted(g.items(), key=lambda kv:-len(kv[1]))[:15]: print(f"  {k}: n={len(v)} sum={round(sum(v),2)}")
    print("\n### position_size vs pnl sanity")
    for r in sel[:5]:
        print("  ", r['symbol'], 'size',r['position_size'],'entry',r['entry_price'],'exit',r['exit_price'],'pnl',r['pnl'],
              'implied', round((float(r['exit_price'])-float(r['entry_price']))*float(r['position_size']),2))
    print("\n### all strategy_trades totals by nature (whole table)")
    g={}
    for r in rows:
        d=parse(r['decision_context']); g.setdefault(d.get('nature'),[]).append(float(r['pnl'] or 0))
    for k in sorted(g,key=str):
        v=g[k]; print(f"  nature={k}: n={len(v)} sum={round(sum(v),2)} avg={round(sum(v)/len(v),3)}")
